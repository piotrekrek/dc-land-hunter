"""The sites table and the sites API against a real PostGIS database (CI and local with PostGIS)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import score_candidates, write_sites
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

SCORED_AT = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="member")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded() -> Iterator[None]:
    with session_scope() as session:
        write_sites(
            session, score_candidates(parcel_sites(), layers(), CFG), scored_at=SCORED_AT, powiat="1421"
        )
    yield


def test_run_writes_sites_and_replaces_previous_run(seeded: None) -> None:
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM sites")).scalar_one() == 3
        write_sites(
            session, score_candidates(parcel_sites()[:1], layers(), CFG), scored_at=SCORED_AT, powiat="1421"
        )
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM sites")).scalar_one() == 1


def test_a_powiat_run_keeps_the_sites_of_other_powiats(seeded: None) -> None:
    # Only the powiat's own rows are replaced: searching 1425 must leave the three sites of 1421 alone.
    with session_scope() as session:
        write_sites(session, [], scored_at=SCORED_AT, powiat="1425")
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM sites WHERE powiat = '1421'")).scalar_one() == 3
    with session_scope() as session:
        write_sites(session, [], scored_at=SCORED_AT, powiat="1421")
        assert session.execute(text("SELECT count(*) FROM sites")).scalar_one() == 0


def test_list_hides_excluded_by_default_and_sorts_by_score(seeded: None, client: TestClient) -> None:
    body = client.get("/api/sites").json()
    assert body["total"] == 2
    scores = [item["score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert all(item["excluded"] is False for item in body["items"])
    assert all(-180 < item["lon"] < 180 and -90 < item["lat"] < 90 for item in body["items"])


def test_list_includes_excluded_on_request(seeded: None, client: TestClient) -> None:
    body = client.get("/api/sites", params={"include_excluded": "true"}).json()
    assert body["total"] == 3
    assert sum(item["excluded"] for item in body["items"]) == 1


def test_list_filters_by_minimum_mw_and_pages(seeded: None, client: TestClient) -> None:
    # 32 ha is about 192 MW, 15 ha is about 90 MW (6 MW/ha), the 4 ha site is excluded anyway.
    assert client.get("/api/sites", params={"min_mw": 150}).json()["total"] == 1
    assert client.get("/api/sites", params={"min_mw": 500}).json()["total"] == 0
    page = client.get("/api/sites", params={"limit": 1, "offset": 1, "include_excluded": "true"}).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["offset"] == 1


@pytest.mark.parametrize("sorting", [{}, {"sort": "area", "order": "asc"}])
def test_cursor_pages_skip_nothing_when_a_row_leaves_between_pages(
    seeded: None, client: TestClient, sorting: dict[str, str]
) -> None:
    base = {"include_excluded": "true", **sorting}
    expected = [
        item["site_id"] for item in client.get("/api/sites", params={**base, "limit": 200}).json()["items"]
    ]
    first = client.get("/api/sites", params={**base, "limit": 1}).json()
    assert first["next_cursor"] is not None
    # A scoring run replaces rows between two pages: the top site disappears.
    with session_scope() as session:
        session.execute(
            text("DELETE FROM sites WHERE site_id = :site_id"), {"site_id": first["items"][0]["site_id"]}
        )
    seen = [item["site_id"] for item in first["items"]]
    cursor = first["next_cursor"]
    while cursor is not None:
        page = client.get("/api/sites", params={**base, "limit": 1, "cursor": cursor}).json()
        seen += [item["site_id"] for item in page["items"]]
        cursor = page["next_cursor"]
    # With an offset the rows would shift and one site would be skipped; with a cursor none is.
    assert seen == expected


def test_cursor_and_offset_cannot_be_combined(seeded: None, client: TestClient) -> None:
    cursor = client.get("/api/sites", params={"limit": 1}).json()["next_cursor"]
    assert client.get("/api/sites", params={"cursor": cursor, "offset": 1}).status_code == 422


def test_list_sorts_ascending_by_area(seeded: None, client: TestClient) -> None:
    body = client.get(
        "/api/sites", params={"sort": "area", "order": "asc", "include_excluded": "true"}
    ).json()
    areas = [item["area_ha"] for item in body["items"]]
    assert areas == sorted(areas)


def test_unknown_sort_is_rejected(seeded: None, client: TestClient) -> None:
    assert client.get("/api/sites", params={"sort": "name; DROP TABLE sites"}).status_code == 422


def test_site_card_has_breakdown_provenance_and_geometry(seeded: None, client: TestClient) -> None:
    top = client.get("/api/sites").json()["items"][0]
    card = client.get(f"/api/sites/{top['site_id']}").json()
    assert card["site_id"] == top["site_id"]
    assert {c["category"] for c in card["categories"]} == {"grid", "zoning", "land", "connectivity", "bonus"}
    assert any(item["key"] for item in card["items"])
    assert card["mw_fact"]["confidence"] == "estimated"
    assert card["mw_fact"]["value"] == card["estimated_mw"]
    assert card["features"]["area_ha"]["source_id"] == "egib"
    assert card["features"]["area_ha"]["confidence"] == "verified"
    assert card["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert card["scored_at"].startswith("2026-10-10")


def test_unknown_site_is_404(seeded: None, client: TestClient) -> None:
    assert client.get("/api/sites/site-nope").status_code == 404


def test_sites_require_login() -> None:
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/sites").status_code == 401


# (teryt, level, name, parent, x0, x1) as full-height strips around the seeded sites, EPSG:2180
_UNITS = [
    ("14", "voivodeship", "mazowieckie", None, 600_000, 700_000),
    ("1421", "powiat", "powiat pruszkowski", "14", 600_000, 700_000),
    ("1421011", "gmina", "Brwinów", "1421", 600_000, 643_000),
    ("1421022", "gmina", "Nadarzyn", "1421", 643_000, 700_000),
]


@pytest.fixture
def places() -> Iterator[None]:
    with session_scope() as session:
        for teryt, level, name, parent, x0, x1 in _UNITS:
            session.execute(
                text(
                    "INSERT INTO admin_units (teryt, level, name, parent_teryt, geom) VALUES (:teryt, :level,"
                    " :name, :parent, ST_Multi(ST_MakeEnvelope(:x0, 400000, :x1, 600000, 2180)))"
                ),
                {"teryt": teryt, "level": level, "name": name, "parent": parent, "x0": x0, "x1": x1},
            )
    yield
    with session_scope() as session:
        session.execute(text("DELETE FROM admin_units"))


def test_list_and_card_name_the_gmina_powiat_and_voivodeship(
    seeded: None, places: None, client: TestClient
) -> None:
    items = client.get("/api/sites", params={"include_excluded": "true"}).json()["items"]
    by_gmina = {item["gmina"]: item for item in items}
    # Two sites lie west of x = 643 000, the 15 ha one east of it.
    assert sorted(by_gmina) == ["Brwinów", "Nadarzyn"]
    assert all(i["powiat"] == "powiat pruszkowski" and i["voivodeship"] == "mazowieckie" for i in items)
    card = client.get(f"/api/sites/{items[0]['site_id']}").json()
    assert (card["gmina"], card["powiat"], card["voivodeship"]) == (
        items[0]["gmina"],
        "powiat pruszkowski",
        "mazowieckie",
    )


def test_places_are_null_outside_any_unit(seeded: None, client: TestClient) -> None:
    body = client.get("/api/sites").json()
    assert body["total"] == 2
    assert all(i["gmina"] is None and i["powiat"] is None and i["voivodeship"] is None for i in body["items"])


def test_list_filters_by_powiat_and_voivodeship(seeded: None, places: None, client: TestClient) -> None:
    assert client.get("/api/sites", params={"powiat": "1421"}).json()["total"] == 2
    assert client.get("/api/sites", params={"powiat": "1425"}).json()["total"] == 0
    assert client.get("/api/sites", params={"voivodeship": "14"}).json()["total"] == 2
    assert client.get("/api/sites", params={"voivodeship": "22"}).json()["total"] == 0
    # The count and the page agree under a filter, also with a cursor.
    page = client.get("/api/sites", params={"powiat": "1421", "limit": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["next_cursor"] is not None


@pytest.mark.parametrize("params", [{"powiat": "14"}, {"powiat": "abcd"}, {"voivodeship": "1421"}])
def test_malformed_teryt_filters_are_rejected(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/api/sites", params=params).status_code == 422


def test_card_carries_the_outline_in_metres_around_its_origin(seeded: None, client: TestClient) -> None:
    site_id = client.get("/api/sites").json()["items"][0]["site_id"]
    frame = client.get(f"/api/sites/{site_id}").json()["map"]
    x0, y0, x1, y1 = frame["bbox_m"]
    ring = frame["parcel"][0]
    assert all(x0 < x < x1 and y0 < y < y1 for x, y in ring)
    # The seeded 32 ha site is 800 x 400 m: the box adds the margin on every side.
    assert x1 - x0 > 800 and y1 - y0 > 400


def test_the_card_orthophoto_is_asked_for_exactly_the_maps_box(
    seeded: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dclh_api.routers import sites

    asked: list[Any] = []

    def fake(wanted: Any, *, user_agent: str, cache: Any) -> bytes:
        asked.append(wanted)
        return b"\xff\xd8\xff\xe0"

    monkeypatch.setattr(sites, "build_client", lambda settings: (None, None))
    monkeypatch.setattr(sites, "fetch_orthophoto", fake)
    site_id = client.get("/api/sites").json()["items"][0]["site_id"]
    frame = client.get(f"/api/sites/{site_id}").json()["map"]
    response = client.get(f"/api/sites/{site_id}/orthophoto")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    (ox, oy), (bx0, by0, bx1, by1) = frame["origin_2180"], frame["bbox_m"]
    assert list(asked[0].bbox) == pytest.approx([ox + bx0, oy + by0, ox + bx1, oy + by1], abs=0.1)


def test_orthophoto_failure_is_a_polish_502_and_unknown_site_a_404(
    seeded: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dclh_api.routers import sites
    from dclh_pipeline.orthophoto import OrthophotoUnavailable

    def down(wanted: Any, *, user_agent: str, cache: Any) -> bytes:
        raise OrthophotoUnavailable("HTTP 404, 0 bytes")

    monkeypatch.setattr(sites, "build_client", lambda settings: (None, None))
    monkeypatch.setattr(sites, "fetch_orthophoto", down)
    site_id = client.get("/api/sites").json()["items"][0]["site_id"]
    assert client.get(f"/api/sites/{site_id}/orthophoto").status_code == 502
    assert client.get("/api/sites/nope/orthophoto").status_code == 404


# --------------------------------------------------------------------------- watches and project cards


def _user(email: str) -> UserRow:
    with session_scope() as session:
        uid = session.execute(
            text(
                "INSERT INTO users (email, password_hash, role) VALUES (:e, 'x', 'member') "
                "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
            ),
            {"e": email},
        ).scalar_one()
    return UserRow(id=uid, email=email, role="member")


def _as(user: UserRow) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ann() -> Iterator[TestClient]:
    yield from _as(_user("ann@example.org"))


@pytest.fixture
def bob() -> Iterator[TestClient]:
    yield from _as(_user("bob@example.org"))


def _site_id(client: TestClient) -> str:
    return str(client.get("/api/sites").json()["items"][0]["site_id"])


def test_watch_is_idempotent_and_per_user(seeded: None, ann: TestClient, bob: TestClient) -> None:
    sid = _site_id(ann)
    assert ann.get(f"/api/sites/{sid}").json()["watched"] is False
    assert ann.put(f"/api/sites/{sid}/watch").json() == {"watched": True}
    assert ann.put(f"/api/sites/{sid}/watch").json() == {"watched": True}
    assert ann.get(f"/api/sites/{sid}").json()["watched"] is True
    assert bob.get(f"/api/sites/{sid}").json()["watched"] is False
    assert ann.delete(f"/api/sites/{sid}/watch").json() == {"watched": False}
    assert ann.delete(f"/api/sites/{sid}/watch").json() == {"watched": False}
    assert ann.get(f"/api/sites/{sid}").json()["watched"] is False


def test_watch_of_an_unknown_site_is_404(seeded: None, ann: TestClient) -> None:
    assert ann.put("/api/sites/nope/watch").status_code == 404
    assert ann.put("/api/sites/nope/project", json={"stage": "new"}).status_code == 404


def test_project_stage_is_recorded_shared_and_idempotent(
    seeded: None, ann: TestClient, bob: TestClient
) -> None:
    sid = _site_id(ann)
    assert ann.get(f"/api/sites/{sid}").json()["stage"] is None
    first = ann.put(f"/api/sites/{sid}/project", json={"stage": "new"}).json()
    assert first["stage"] == "new" and first["owner_note"] is None
    assert ann.put(f"/api/sites/{sid}/project", json={"stage": "new"}).json()["stage"] == "new"
    assert bob.put(f"/api/sites/{sid}/project", json={"stage": "analysis"}).json()["stage"] == "analysis"
    assert ann.get(f"/api/sites/{sid}").json()["stage"] == "analysis"
    with session_scope() as session:
        who = session.execute(
            text(
                "SELECT u.email FROM project_cards p JOIN users u ON u.id = p.updated_by WHERE p.site_id = :s"
            ),
            {"s": sid},
        ).scalar_one()
    assert who == "bob@example.org"
    assert ann.put(f"/api/sites/{sid}/project", json={"stage": "bogus"}).status_code == 422


def test_note_is_kept_across_stage_changes_and_deleted_with_the_project(
    seeded: None, ann: TestClient
) -> None:
    sid = _site_id(ann)
    ann.put(f"/api/sites/{sid}/project", json={"stage": "new", "owner_note": "  zadzwonić do gminy "})
    assert ann.put(f"/api/sites/{sid}/project", json={"stage": "negotiation"}).json()["owner_note"] == (
        "zadzwonić do gminy"
    )
    assert ann.get(f"/api/sites/{sid}").json()["project_note"] == "zadzwonić do gminy"
    assert (
        ann.put(f"/api/sites/{sid}/project", json={"stage": "negotiation", "owner_note": ""}).json()[
            "owner_note"
        ]
        is None
    )
    ann.put(f"/api/sites/{sid}/project", json={"stage": "new", "owner_note": "x"})
    assert ann.delete(f"/api/sites/{sid}/project").status_code == 204
    assert ann.delete(f"/api/sites/{sid}/project").status_code == 204
    body = ann.get(f"/api/sites/{sid}").json()
    assert body["stage"] is None and body["project_note"] is None
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM project_cards")).scalar_one() == 0


def test_written_sites_carry_profile_verdicts(seeded: None, client: TestClient) -> None:
    body = client.get("/api/sites", params={"include_excluded": "true"}).json()
    for item in body["items"]:
        assert set(item["profile_categories"]) == {"built", "container"}
    detail = client.get(f"/api/sites/{body['items'][0]['site_id']}").json()
    assert set(detail["profiles"]) == {"built", "container"}


def test_hide_red_drops_the_sites_the_profile_rejects(seeded: None, client: TestClient) -> None:
    everything = client.get("/api/sites", params={"include_excluded": "true"}).json()["items"]
    victim = everything[0]["site_id"]
    with session_scope() as session:
        session.execute(text("UPDATE sites SET verdict_built = 'red' WHERE site_id = :id"), {"id": victim})
    params = {"include_excluded": "true", "profile": "built", "hide_red": "true"}
    kept = [i["site_id"] for i in client.get("/api/sites", params=params).json()["items"]]
    assert victim not in kept and len(kept) == len(everything) - 1
    # The other profile is untouched, and without a profile the switch has nothing to apply to.
    other = client.get("/api/sites", params={**params, "profile": "container"}).json()["items"]
    assert victim in [i["site_id"] for i in other]
    assert client.get("/api/sites", params={"hide_red": "true"}).status_code == 422
