"""Sprawdź działkę against a real database: the API, the stored progress and result, and the job (pipeline stubbed)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box
from sqlalchemy import text

from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline import check_job
from dclh_pipeline.check_job import DbCheckReporter, create_check, initial_steps, run_check_job, save_result
from dclh_pipeline.check_sheet import build_sheet
from dclh_pipeline.config import load_scoring
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow, UserRow
from dclh_pipeline.jobs import CHECK_INTERRUPTED_PL, claim_next, fail_interrupted
from dclh_pipeline.model.features import SiteLayers, build_site_features
from dclh_pipeline.model.sites import Parcel, cluster_sites
from dclh_pipeline.parcel_check import (
    ParcelCheck,
    Resolution,
    ResolvedParcel,
    next_steps,
    unknowns,
    verdict,
)
from dclh_pipeline.parcel_input import InputIssue
from dclh_pipeline.response_cache import ResponseCache
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.run import ScoredSite

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

CFG = load_scoring()
ID = "306201_1.0002.1573"


def as_user(email: str = "ala@example.org", role: str = "member") -> TestClient:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email=email, role=role)
    return TestClient(app)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with as_user() as c:
        yield c


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            for table in ("parcel_checks", "check_step_stats", "jobs", "runs"):
                session.execute(text(f"DELETE FROM {table}"))

    wipe()
    yield
    wipe()


def resolved(parcel_id: str = ID) -> ResolvedParcel:
    return ResolvedParcel(
        parcel_id,
        "3062",
        "wielkopolskie",
        "powiat Konin",
        "Konin (miasto)",
        "0002",
        box(448_900, 485_800, 449_000, 485_900),
        1.0,
        18.0,
        52.0,
        True,
        parcel_id,
    )


def parcel_check(parcel: ResolvedParcel | None = None) -> ParcelCheck:
    parcel = parcel or resolved()
    site = cluster_sites(
        [Parcel(parcel.parcel_id, None, parcel.geom)], mw_per_ha=CFG.target.mw_per_ha, split_suggestion_ha=80
    )[0]
    features = build_site_features(site, SiteLayers(), CFG)  # nothing loaded: everything unknown
    scored = ScoredSite(site=site, features=features, score=score_site(features, CFG))
    return ParcelCheck(
        parcel=parcel,
        site=site,
        scored=scored,
        verdict=verdict(scored.score),
        unknowns=unknowns(scored, CFG, failed_steps=["flood"]),
        next_steps=next_steps(parcel),
        steps=[],
        sheet=build_sheet(
            parcel.geom,
            [("forest", "Las", "polygon", [box(448_950, 485_850, 449_100, 485_950)])],
            margin_m=500,
        ),
        layer_ages={"osm_power": datetime(2026, 10, 12, tzinfo=UTC), "isok": None},
        outside_aoi=False,
    )


def open_check(parcels: list[str] | None = None, by: str = "ala@example.org") -> str:
    inputs = [{"kind": "id", "parcel_id": p, "text": p} for p in (parcels or [ID])]
    with session_scope() as session:
        return create_check(session, by, inputs)


# ----------------------------------------------------------------------------------------------- create


def test_a_check_needs_a_login() -> None:
    with TestClient(create_app()) as anonymous:
        assert anonymous.post("/api/checks", json={"parcels": [ID]}).status_code == 401
        assert anonymous.get("/api/checks").status_code == 401
        assert anonymous.post("/api/parcels/resolve", json={"text": ID}).status_code == 401


def test_starting_a_check_queues_a_job_and_returns_its_id(client: TestClient) -> None:
    response = client.post("/api/checks", json={"parcels": [ID, "142104_2.0013.562/2"]})
    assert response.status_code == 202
    check_id = response.json()["id"]
    with session_scope() as session:
        row = (
            session.execute(
                text(
                    "SELECT status, requested_by, inputs, job_id FROM parcel_checks WHERE id = CAST(:i AS uuid)"
                ),
                {"i": check_id},
            )
            .mappings()
            .one()
        )
        job = session.get(JobRow, row["job_id"])
        assert row["status"] == "pending" and row["requested_by"] == "ala@example.org"
        assert [i["parcel_id"] for i in row["inputs"]] == [ID, "142104_2.0013.562/2"]
        assert job is not None and job.kind == "check_parcel" and job.payload == {"check_id": check_id}


def test_a_point_is_accepted_and_kept_as_a_point(client: TestClient) -> None:
    response = client.post("/api/checks", json={"points": [{"lat": 52.2297, "lon": 20.9511}]})
    assert response.status_code == 202
    with session_scope() as session:
        inputs = session.execute(text("SELECT inputs FROM parcel_checks")).scalar_one()
    assert inputs[0]["kind"] == "point" and inputs[0]["lat"] == 52.2297


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"parcels": ["141/9"]},  # not an id
        {"parcels": ["52.2297 20.9511"]},  # coordinates belong in `points`
        {"points": [{"lat": 10, "lon": 20}]},  # outside Poland
        {"parcels": [f"142104_2.0013.{i}" for i in range(1, 22)]},  # 21 ids
    ],
)
def test_bad_input_is_a_422(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/checks", json=body).status_code == 422
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM parcel_checks")).scalar_one() == 0


def test_a_user_cannot_have_more_than_three_checks_open(client: TestClient) -> None:
    for _ in range(3):
        assert client.post("/api/checks", json={"parcels": [ID]}).status_code == 202
    assert client.post("/api/checks", json={"parcels": [ID]}).status_code == 429
    with as_user("other@example.org") as other:
        assert other.post("/api/checks", json={"parcels": [ID]}).status_code == 202  # the limit is per user


# ----------------------------------------------------------------------------------------------- read


def test_an_open_check_shows_an_estimate_from_the_defaults_then_from_history(client: TestClient) -> None:
    check_id = open_check()
    first = client.get(f"/api/checks/{check_id}").json()
    assert first["status"] == "pending" and first["sites"] == []
    assert first["parcels"] == [ID]
    # The page has the whole plan from the start: a progress bar of 10 steps, none done, not "0 z 0".
    assert len(first["steps"]) == 10 and {s["status"] for s in first["steps"]} == {"waiting"}
    assert first["steps"][0]["key"] == "static" and first["steps"][0]["parcel"] is None
    assert first["estimate_seconds"] and first["estimate_seconds"] > 20  # static layers and every step
    with session_scope() as session:
        for seconds in (1.0, 3.0, 2.0):
            session.execute(
                text(
                    "INSERT INTO check_step_stats (step_key, warm, seconds) VALUES ('neighbours', false, :s)"
                ),
                {"s": seconds},
            )
    second = client.get(f"/api/checks/{check_id}").json()
    assert (
        second["estimate_seconds"] == first["estimate_seconds"] - 8 + 2
    )  # default 8 s replaced by the median 2 s


def test_unknown_and_malformed_check_ids_are_404(client: TestClient) -> None:
    assert client.get("/api/checks/00000000-0000-4000-8000-000000000000").status_code == 404
    assert client.get("/api/checks/not-a-uuid").status_code == 404
    assert client.get("/api/checks/00000000-0000-4000-8000-000000000000/sites/0").status_code == 404


def test_a_saved_result_comes_back_as_a_site_card_with_the_verdict_and_the_sheet(client: TestClient) -> None:
    check_id = open_check()
    result = parcel_check()
    with session_scope() as session:
        save_result(session, check_id, 0, result, datetime(2026, 10, 25, 9, 0, tzinfo=UTC))
        session.execute(
            text("UPDATE parcel_checks SET status = 'done' WHERE id = CAST(:i AS uuid)"), {"i": check_id}
        )
    summary = client.get(f"/api/checks/{check_id}").json()
    assert summary["status"] == "done" and summary["estimate_seconds"] is None
    site = summary["sites"][0]
    assert (
        site["parcel_id"] == ID
        and site["commune"] == "Konin (miasto)"
        and site["verdict"]["kind"] == result.verdict.kind
    )

    detail = client.get(f"/api/checks/{check_id}/sites/0").json()
    assert (
        detail["parcel_ids"] == [ID]
        and detail["check_id"] == check_id
        and detail["requested_by"] == "ala@example.org"
    )
    assert (
        detail["items"] and detail["categories"] is not None and "features" in detail
    )  # the site card's own fields
    assert detail["geometry"]["type"] == "Polygon"
    assert detail["verdict"]["text_pl"] == result.verdict.text_pl
    assert any(u["label_pl"] == "Nie sprawdzono: strefa zalewowa" for u in detail["unknowns"])
    assert {s["key"] for s in detail["next_steps"]} == {"mpzp", "dso", "kw", "ooss"}
    assert detail["sheet"]["layers"][0]["key"] == "forest" and detail["sheet"]["parcel"]
    assert detail["layer_ages"]["isok"] is None and detail["layer_ages"]["osm_power"].startswith("2026-10-12")
    assert detail["parcel"]["obreb"] == "0002" and detail["outside_aoi"] is False


def test_the_recent_list_of_a_member_has_only_their_own_checks_newest_first(client: TestClient) -> None:
    other = open_check(by="ola@example.org")
    older = open_check()
    newer = open_check()
    with session_scope() as session:
        save_result(session, newer, 0, parcel_check(), datetime(2026, 10, 25, tzinfo=UTC))
        session.execute(
            text("UPDATE parcel_checks SET status = 'done' WHERE id = CAST(:i AS uuid)"), {"i": newer}
        )
    items = client.get("/api/checks").json()["items"]
    assert [i["id"] for i in items] == [newer, older] and other not in {i["id"] for i in items}
    assert items[0]["parcels"] == [ID] and items[0]["verdict"] is not None and items[1]["verdict"] is None


def test_an_admin_sees_everyones_checks(client: TestClient) -> None:
    theirs = open_check(by="ola@example.org")
    mine = open_check(by="admin@example.org")
    with as_user("admin@example.org", "admin") as admin:
        ids = [i["id"] for i in admin.get("/api/checks").json()["items"]]
        assert ids == [mine, theirs]
        assert admin.get(f"/api/checks/{theirs}").status_code == 200  # and can open any of them


def test_a_member_cannot_open_somebody_elses_check_and_is_told_it_does_not_exist(client: TestClient) -> None:
    theirs = open_check(by="ola@example.org")
    with session_scope() as session:
        save_result(session, theirs, 0, parcel_check(), datetime(2026, 10, 25, tzinfo=UTC))
        session.execute(
            text("UPDATE parcel_checks SET status = 'done' WHERE id = CAST(:i AS uuid)"), {"i": theirs}
        )
    missing = client.get("/api/checks/00000000-0000-4000-8000-000000000000")
    for url in (f"/api/checks/{theirs}", f"/api/checks/{theirs}/sites/0"):
        response = client.get(url)
        assert response.status_code == 404
        assert response.json() == missing.json()  # the same answer as for a check that does not exist
    with as_user("ola@example.org") as owner:
        assert owner.get(f"/api/checks/{theirs}/sites/0").status_code == 200  # the owner still can


def test_saving_the_same_parcel_twice_keeps_the_first(client: TestClient) -> None:
    check_id = open_check()
    with session_scope() as session:
        save_result(session, check_id, 0, parcel_check(), datetime(2026, 10, 25, tzinfo=UTC))
        save_result(session, check_id, 0, parcel_check(), datetime(2026, 10, 26, tzinfo=UTC))
        assert session.execute(text("SELECT count(*) FROM parcel_check_sites")).scalar_one() == 1


# ------------------------------------------------------------------------------------------ progress


def steps_in_db(check_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        return list(
            session.execute(
                text("SELECT steps FROM parcel_checks WHERE id = CAST(:i AS uuid)"), {"i": check_id}
            ).scalar_one()
        )


def test_every_step_change_is_visible_to_another_connection_at_once(tmp_path: Any) -> None:
    check_id = open_check()
    parcel = resolved()
    steps = initial_steps([parcel], static_cached=True)
    cache = ResponseCache(tmp_path, SimpleNamespace(total_seconds=lambda: 1.0))  # type: ignore[arg-type]
    reporter = DbCheckReporter(check_id, steps, cache)
    reporter.step_start(0, "flood")
    running = {(s["parcel"], s["key"]): s["status"] for s in steps_in_db(check_id)}
    assert (
        running[(0, "flood")] == "running"
        and running[(0, "mining")] == "waiting"
        and running[(0, "parcel")] == "done"
    )
    cache.misses += 2  # two requests went out: not a warm step
    reporter.step_end(0, "flood", ok=True, seconds=6.5, finding="Działka poza strefami zalewowymi.")
    done = next(s for s in steps_in_db(check_id) if s["key"] == "flood")
    assert (
        done["status"] == "done" and done["seconds"] == 6.5 and done["finding_pl"].startswith("Działka poza")
    )
    with session_scope() as session:
        stats = session.execute(text("SELECT step_key, warm, seconds FROM check_step_stats")).all()
    assert [tuple(r) for r in stats] == [("flood", False, 6.5)]


def test_a_failed_step_keeps_its_error_and_adds_no_timing(tmp_path: Any) -> None:
    check_id = open_check()
    reporter = DbCheckReporter(check_id, initial_steps([resolved()], static_cached=True), None)
    reporter.step_start(0, "flood")
    reporter.step_end(0, "flood", ok=False, seconds=60.0, error="HTTPStatusError: 504 Gateway Timeout")
    flood = next(s for s in steps_in_db(check_id) if s["key"] == "flood")
    assert flood["status"] == "failed" and "504" in flood["error"]
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM check_step_stats")).scalar_one() == 0


def test_a_step_answered_entirely_from_the_cache_is_recorded_warm(tmp_path: Any) -> None:
    check_id = open_check()
    cache = ResponseCache(tmp_path, SimpleNamespace(total_seconds=lambda: 1.0))  # type: ignore[arg-type]
    reporter = DbCheckReporter(check_id, initial_steps([resolved()], static_cached=True), cache)
    reporter.step_start(0, "bdot10k")
    cache.hits += 3
    reporter.step_end(0, "bdot10k", ok=True, seconds=1.1)
    with session_scope() as session:
        assert session.execute(text("SELECT warm FROM check_step_stats")).scalar_one() is True


# --------------------------------------------------------------------------------------------- the job


class FakeClient:
    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> pytest.MonkeyPatch:
    monkeypatch.setattr(check_job, "build_client", lambda settings: (FakeClient(), None))
    monkeypatch.setattr(
        check_job,
        "cached_static_layers",
        lambda *a: (SimpleNamespace(osm_snapshot=None, prg_snapshot=None), False),
    )
    monkeypatch.setattr(check_job, "_static_is_cached", lambda store: False)
    monkeypatch.setattr(
        check_job, "get_settings", lambda: SimpleNamespace(contact_email="a@example.org", raw_dir=tmp_path)
    )
    return monkeypatch


def test_the_job_runs_every_parcel_stores_each_result_and_finishes_done(stubbed: pytest.MonkeyPatch) -> None:
    other = "142104_2.0013.562/2"
    parcels = [resolved(ID), resolved(other)]
    stubbed.setattr(
        check_job, "resolve_parcels", lambda client, parsed, aoi: Resolution(items=parcels, issues=[])
    )
    seen: list[int] = []

    def fake_check(parcel: ResolvedParcel, index: int, ctx: Any, reporter: Any) -> ParcelCheck:
        seen.append(index)
        reporter.step_start(index, "scoring")
        reporter.step_end(index, "scoring", ok=True, seconds=0.5, finding="Wynik 10 pkt.")
        return parcel_check(parcel)

    stubbed.setattr(check_job, "check_parcel", fake_check)
    check_id = open_check([ID, other])
    summary = run_check_job(None, {"check_id": check_id})  # type: ignore[arg-type]
    assert summary["parcels"] == 2 and seen == [0, 1]
    with session_scope() as session:
        row = (
            session.execute(
                text(
                    "SELECT status, started_at, finished_at, error FROM parcel_checks WHERE id = CAST(:i AS uuid)"
                ),
                {"i": check_id},
            )
            .mappings()
            .one()
        )
        sites = session.execute(text("SELECT n, site_id FROM parcel_check_sites ORDER BY n")).all()
    assert row["status"] == "done" and row["started_at"] and row["finished_at"] and row["error"] is None
    assert [s[0] for s in sites] == [0, 1]
    scoring = [s for s in steps_in_db(check_id) if s["key"] == "scoring"]
    assert all(s["status"] == "done" for s in scoring)
    static = next(s for s in steps_in_db(check_id) if s["key"] == "static")
    assert static["status"] == "done"  # shown as a step of its own the first time


def test_when_no_parcel_is_found_the_check_fails_with_the_reason(stubbed: pytest.MonkeyPatch) -> None:
    issue = InputIssue(ID, f"Nie znaleziono działki {ID}.")
    stubbed.setattr(
        check_job, "resolve_parcels", lambda client, parsed, aoi: Resolution(items=[], issues=[issue])
    )
    check_id = open_check()
    with pytest.raises(ValueError, match="Nie znaleziono"):
        run_check_job(None, {"check_id": check_id})  # type: ignore[arg-type]
    with session_scope() as session:
        row = (
            session.execute(
                text("SELECT status, error, finished_at FROM parcel_checks WHERE id = CAST(:i AS uuid)"),
                {"i": check_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "failed" and "Nie znaleziono" in row["error"] and row["finished_at"]


def test_a_failure_on_the_second_parcel_keeps_the_first_result(stubbed: pytest.MonkeyPatch) -> None:
    parcels = [resolved(ID), resolved("142104_2.0013.562/2")]
    stubbed.setattr(
        check_job, "resolve_parcels", lambda client, parsed, aoi: Resolution(items=parcels, issues=[])
    )

    def flaky(parcel: ResolvedParcel, index: int, ctx: Any, reporter: Any) -> ParcelCheck:
        if index == 1:
            raise RuntimeError("no powiat with TERYT '1421' in the PRG snapshot")
        return parcel_check(parcel)

    stubbed.setattr(check_job, "check_parcel", flaky)
    check_id = open_check([ID, "142104_2.0013.562/2"])
    with pytest.raises(RuntimeError):
        run_check_job(None, {"check_id": check_id})  # type: ignore[arg-type]
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM parcel_check_sites")).scalar_one() == 1
        status = session.execute(text("SELECT status FROM parcel_checks")).scalar_one()
    assert status == "failed"


def test_a_worker_restart_fails_the_running_check_so_the_page_does_not_wait_for_ever() -> None:
    check_id = open_check()
    with session_scope() as session:
        job = claim_next(session, kinds=["check_parcel"])
        assert job is not None
        session.execute(
            text("UPDATE parcel_checks SET status = 'running' WHERE id = CAST(:i AS uuid)"), {"i": check_id}
        )
    with session_scope() as session:
        assert fail_interrupted(session, kinds=["check_parcel"]) == 1
    with session_scope() as session:
        row = (
            session.execute(
                text("SELECT status, error, finished_at FROM parcel_checks WHERE id = CAST(:i AS uuid)"),
                {"i": check_id},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "failed" and row["error"] == CHECK_INTERRUPTED_PL and row["finished_at"]


def test_a_finished_check_is_left_alone_by_a_restart() -> None:
    check_id = open_check()
    with session_scope() as session:
        claim_next(session, kinds=["check_parcel"])
        session.execute(
            text("UPDATE parcel_checks SET status = 'done' WHERE id = CAST(:i AS uuid)"), {"i": check_id}
        )
    with session_scope() as session:
        fail_interrupted(session, kinds=["check_parcel"])
        assert session.execute(text("SELECT status FROM parcel_checks")).scalar_one() == "done"


def test_the_stored_json_round_trips() -> None:
    check_id = open_check()
    with session_scope() as session:
        save_result(session, check_id, 0, parcel_check(), datetime(2026, 10, 25, tzinfo=UTC))
        sheet = session.execute(text("SELECT sheet FROM parcel_check_sites")).scalar_one()
    assert json.loads(json.dumps(sheet))["bbox_m"][0] < 0


# ------------------------------------------------------------------------------------------- resolve


@pytest.fixture
def resolve_stub(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    from dclh_api.routers import checks

    monkeypatch.setattr(checks, "build_client", lambda settings: (FakeClient(), None))
    monkeypatch.setattr(checks, "get_settings", lambda: SimpleNamespace(contact_email="a@example.org"))
    return monkeypatch


def test_resolve_returns_the_parcel_with_a_wgs84_outline_and_the_issues(
    client: TestClient, resolve_stub: pytest.MonkeyPatch
) -> None:
    from dclh_api.routers import checks

    seen: dict[str, Any] = {}

    def fake(client_: Any, parsed: Any, aoi: Any) -> Resolution:
        seen["inputs"] = [i.parcel_id for i in parsed.items]
        return Resolution(items=[resolved()], issues=[InputIssue("141/9", "Brakuje obrębu.")], duplicates=1)

    resolve_stub.setattr(checks, "resolve_parcels", fake)
    body = client.post("/api/parcels/resolve", json={"text": f"{ID}\n141/9"}).json()
    assert seen["inputs"] == [ID]  # the text was parsed on the server
    parcel = body["items"][0]
    assert parcel["parcel_id"] == ID and parcel["commune"] == "Konin (miasto)" and parcel["in_aoi"] is True
    ring = parcel["outline"]["coordinates"][0]
    assert parcel["outline"]["type"] == "Polygon" and all(
        14 < lon < 25 and 48 < lat < 56 for lon, lat in ring
    )
    assert body["issues"] == [{"text": "141/9", "message_pl": "Brakuje obrębu."}] and body["duplicates"] == 1


def test_resolve_without_a_contact_address_is_a_clear_503(client: TestClient) -> None:
    response = client.post("/api/parcels/resolve", json={"text": ID})
    assert response.status_code == 503 and "DCLH_CONTACT_EMAIL" in response.json()["detail"]


# ------------------------------------------------------------------------------------------- memory


def memory_stub(monkeypatch: pytest.MonkeyPatch, fail_times: int) -> list[str]:
    """`cached_static_layers` raises MemoryError `fail_times` times, then works; returns the notes shown."""
    attempts = {"n": 0}
    notes: list[str] = []

    def flaky(*args: Any) -> tuple[Any, bool]:
        attempts["n"] += 1
        if attempts["n"] <= fail_times:
            raise MemoryError("only 742 MB of memory is available, the OSM parse wants at least 800 MB")
        return SimpleNamespace(osm_snapshot=None, prg_snapshot=None), False

    monkeypatch.setattr(check_job, "cached_static_layers", flaky)
    monkeypatch.setattr(check_job, "MEMORY_WAIT_S", 0.0)
    original = DbCheckReporter.note
    monkeypatch.setattr(
        DbCheckReporter, "note", lambda self, p, k, t: (notes.append(t), original(self, p, k, t))[0]
    )
    return notes


def test_a_check_waits_for_free_memory_and_then_runs(stubbed: pytest.MonkeyPatch) -> None:
    notes = memory_stub(stubbed, fail_times=2)
    stubbed.setattr(
        check_job, "resolve_parcels", lambda client, parsed, aoi: Resolution(items=[resolved()], issues=[])
    )
    stubbed.setattr(check_job, "check_parcel", lambda parcel, index, ctx, reporter: parcel_check(parcel))
    check_id = open_check()
    run_check_job(None, {"check_id": check_id})  # type: ignore[arg-type]
    assert (
        notes == ["Czekam na wolną pamięć serwera (trwa inne zadanie)…"] * 2
    )  # the page is told while it waits
    with session_scope() as session:
        assert session.execute(text("SELECT status FROM parcel_checks")).scalar_one() == "done"
    static = next(s for s in steps_in_db(check_id) if s["key"] == "static")
    assert static["status"] == "done" and "Czekam" not in (static["finding_pl"] or "")


def test_a_check_that_never_gets_memory_fails_with_a_message_a_person_can_act_on(
    stubbed: pytest.MonkeyPatch,
) -> None:
    memory_stub(stubbed, fail_times=99)
    stubbed.setattr(check_job, "MEMORY_ATTEMPTS", 3)
    stubbed.setattr(
        check_job, "resolve_parcels", lambda client, parsed, aoi: Resolution(items=[resolved()], issues=[])
    )
    check_id = open_check()
    with pytest.raises(MemoryError):
        run_check_job(None, {"check_id": check_id})  # type: ignore[arg-type]
    with session_scope() as session:
        row = session.execute(text("SELECT status, error FROM parcel_checks")).mappings().one()
    assert row["status"] == "failed"
    assert (
        row["error"] == check_job.CHECK_MEMORY_PL and "MB" not in row["error"]
    )  # no engineering detail for the reader


def test_the_worker_tells_its_idle_hook_how_long_it_has_been_idle() -> None:
    from dclh_pipeline.jobs import work

    seen: list[float] = []
    assert work(once=True, kinds=["check_parcel"], on_idle=seen.append) == 0
    assert len(seen) == 1 and 0 <= seen[0] < 5


def test_the_idle_hook_is_not_called_while_jobs_are_running(stubbed: pytest.MonkeyPatch) -> None:
    from dclh_pipeline import jobs

    stubbed.setattr(
        check_job, "resolve_parcels", lambda client, parsed, aoi: Resolution(items=[resolved()], issues=[])
    )
    stubbed.setattr(check_job, "check_parcel", lambda parcel, index, ctx, reporter: parcel_check(parcel))
    open_check()
    calls: list[float] = []
    assert jobs.work(once=True, kinds=["check_parcel"], on_idle=calls.append) == 1
    assert len(calls) == 1  # once, after the queue emptied
