"""The Powiaty tab's endpoints against a real database (the checked/priority rules live in SQL)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

SQUARE = "ST_Multi(ST_GeomFromText('POLYGON((0 0, 1 0, 1 1, 0 0))', 2180))"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email="ala@example.org", role="member")
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def tables() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            for table in ("powiat_priority", "search_runs", "admin_units"):
                session.execute(text(f"DELETE FROM {table}"))

    wipe()
    with session_scope() as session:
        for teryt, level, name, parent in (
            ("08", "voivodeship", "lubuskie", None),
            ("14", "voivodeship", "mazowieckie", None),
            ("0805", "powiat", "powiat słubicki", "08"),
            ("0801", "powiat", "powiat gorzowski", "08"),
            ("1465", "powiat", "powiat Warszawa", "14"),
            ("080501", "gmina", "Słubice", "0805"),
        ):
            session.execute(
                text(
                    "INSERT INTO admin_units (teryt, level, name, parent_teryt, geom) "
                    f"VALUES (:t, :l, :n, :p, {SQUARE})"
                ),
                {"t": teryt, "l": level, "n": name, "p": parent},
            )
        session.execute(
            text(
                "INSERT INTO search_runs (teryt, last_attempt_at, last_ok_at, status, sites) "
                "VALUES ('0801', now(), now(), 'ok', 4)"
            )
        )
        session.execute(
            text(
                "INSERT INTO search_runs (teryt, last_attempt_at, last_ok_at, status, error) "
                "VALUES ('1465', now(), NULL, 'failed', :e)"
            ),
            {"e": "x" * 1_000},
        )
    yield
    wipe()


def items(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get("/api/powiats")
    assert response.status_code == 200
    return {i["teryt"]: i for i in response.json()["items"]}


def test_lists_powiats_only_with_voivodeship_and_check_state(client: TestClient) -> None:
    by_code = items(client)
    assert set(by_code) == {"0805", "0801", "1465"}  # the gmina and the voivodeships are not listed
    assert by_code["0805"]["voivodeship_name"] == "lubuskie" and by_code["0805"]["checked"] is False
    assert by_code["0801"]["checked"] is True and by_code["0801"]["sites"] == 4
    assert by_code["1465"]["checked"] is False and by_code["1465"]["status"] == "failed"
    assert len(by_code["1465"]["error"]) == 300


def test_the_list_is_ordered_by_voivodeship_then_name(client: TestClient) -> None:
    order = [i["teryt"] for i in client.get("/api/powiats").json()["items"]]
    assert order == ["0801", "0805", "1465"]


def test_put_replaces_the_priority_list(client: TestClient) -> None:
    first = client.put("/api/powiats/priority", json={"teryts": ["0805", "1465"]})
    assert first.status_code == 200
    assert {i["teryt"] for i in first.json()["items"] if i["priority"]} == {"0805", "1465"}
    second = client.put("/api/powiats/priority", json={"teryts": ["1465", "1465"]})
    assert {i["teryt"] for i in second.json()["items"] if i["priority"]} == {"1465"}
    with session_scope() as session:
        rows = session.execute(text("SELECT teryt, added_by FROM powiat_priority")).all()
    assert [tuple(r) for r in rows] == [("1465", "ala@example.org")]


def test_put_keeps_the_added_at_of_codes_that_stay(client: TestClient) -> None:
    client.put("/api/powiats/priority", json={"teryts": ["1465"]})
    with session_scope() as session:
        session.execute(text("UPDATE powiat_priority SET added_at = '2026-01-01T00:00:00Z'"))
    client.put("/api/powiats/priority", json={"teryts": ["1465", "0805"]})
    with session_scope() as session:
        stamps = dict(session.execute(text("SELECT teryt, added_at::date::text FROM powiat_priority")).all())
    assert stamps["1465"] == "2026-01-01" and stamps["0805"] != "2026-01-01"


def test_put_rejects_unknown_codes_and_changes_nothing(client: TestClient) -> None:
    client.put("/api/powiats/priority", json={"teryts": ["0805"]})
    response = client.put("/api/powiats/priority", json={"teryts": ["0805", "9999", "080501"]})
    assert (
        response.status_code == 422
        and "9999" in response.json()["detail"]
        and "080501" in response.json()["detail"]
    )
    assert items(client)["0805"]["priority"] is True


def test_an_empty_list_clears_the_priority(client: TestClient) -> None:
    client.put("/api/powiats/priority", json={"teryts": ["0805"]})
    response = client.put("/api/powiats/priority", json={"teryts": []})
    assert response.status_code == 200 and not any(i["priority"] for i in response.json()["items"])


def test_both_endpoints_need_a_login() -> None:
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/powiats").status_code == 401
        assert anonymous.put("/api/powiats/priority", json={"teryts": []}).status_code == 401


def test_next_up_lists_unsearched_priority_first_then_the_stalest(client: TestClient) -> None:
    with session_scope() as session:
        session.execute(text("DELETE FROM search_runs"))
        session.execute(
            text(
                "INSERT INTO search_runs (teryt, last_attempt_at, last_ok_at, status) VALUES "
                "('0801', now() - interval '3 days', now() - interval '3 days', 'ok'),"
                "('0805', now() - interval '1 day', now() - interval '1 day', 'ok')"
            )
        )
    first = client.put("/api/powiats/priority", json={"teryts": ["1465"]}).json()
    assert first["next_up"] == ["1465", "0801", "0805"]  # 1465 forced first; then the stalest search
    # A priority powiat with a successful search is no longer forced.
    again = client.put("/api/powiats/priority", json={"teryts": ["0805"]}).json()
    assert again["next_up"] == ["1465", "0801", "0805"]
    assert client.get("/api/powiats").json()["next_up"] == again["next_up"]


def test_next_up_skips_powiats_that_already_have_a_search_job_waiting(client: TestClient) -> None:
    client.put("/api/powiats/priority", json={"teryts": ["0805", "1465"]})
    with session_scope() as session:
        session.execute(
            text("INSERT INTO jobs (kind, status, payload) VALUES ('search', 'pending', CAST(:p AS jsonb))"),
            {"p": json.dumps({"powiat": "0805"})},
        )
    try:
        assert client.get("/api/powiats").json()["next_up"][0] == "1465"
    finally:
        with session_scope() as session:
            session.execute(text("DELETE FROM jobs"))


def test_next_up_shows_at_most_four() -> None:
    from dclh_api.routers.powiats import NEXT_UP

    assert NEXT_UP == 4
