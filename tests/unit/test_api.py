"""API tests without a database: the app must degrade, not crash."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dclh_api.deps import db_session
from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline.db.models import UserRow

UNREACHABLE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none?connect_timeout=1"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    engine = create_engine(UNREACHABLE)

    def no_db() -> Iterator[Session]:
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_session] = no_db
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="admin")
    with TestClient(app) as c:
        yield c


class _StubResult:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _StubResult:
        return self

    def first(self) -> dict[str, Any]:
        return self._row


class _StubSession:
    """Returns one canned row for any query, so a route can be tested with a malformed database row."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def execute(self, *_args: object, **_kwargs: object) -> _StubResult:
        return _StubResult(self._row)


@pytest.mark.parametrize("detail", [{"features": {}}, None], ids=["no-score-key", "detail-null"])
def test_malformed_site_row_is_a_logged_polish_500_without_a_traceback(
    detail: dict[str, Any] | None, caplog: pytest.LogCaptureFixture
) -> None:
    row = {
        "detail": detail,
        "geojson": '{"type": "Point", "coordinates": [0, 0]}',
        "parcel_ids": [],
        "scored_at": None,
    }
    app = create_app()

    def stub_db() -> Iterator[Session]:
        yield cast(Session, _StubSession(row))

    app.dependency_overrides[db_session] = stub_db
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="admin")
    with TestClient(app, raise_server_exceptions=False) as client, caplog.at_level(logging.ERROR):
        response = client.get("/api/sites/site-broken")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Nie udało się wczytać danych tej lokalizacji."}
    assert (
        "Traceback" not in response.text
        and "KeyError" not in response.text
        and "TypeError" not in response.text
    )
    assert any("Malformed sites row site-broken" in r.getMessage() for r in caplog.records)


def test_health_degrades_without_db(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["database"] is False


def test_sources_listed_from_config_without_db(client: TestClient) -> None:
    body = client.get("/api/sources").json()
    assert body["database"] is False
    ids = {s["id"] for s in body["sources"]}
    assert {"egib", "gdos", "ekw"} <= ids
    ekw = next(s for s in body["sources"] if s["id"] == "ekw")
    assert ekw["plan"] == "manual" and ekw["manual_url"].startswith("https://")


def test_sites_cursor_is_checked_before_the_database(client: TestClient) -> None:
    # Validation runs before any query, so these answer 422 even with no database.
    assert client.get("/api/sites", params={"cursor": "!!!"}).status_code == 422
    assert client.get("/api/sites", params={"cursor": "!!!", "offset": 50}).status_code == 422


def test_scoring_config(client: TestClient) -> None:
    body = client.get("/api/config/scoring").json()
    assert body["weights"]["grid"] == 45
    assert body["connection_cost"]["verified"] is False


def test_openapi_is_committed_and_current(client: TestClient) -> None:
    import json
    from pathlib import Path

    committed = json.loads((Path(__file__).parents[2] / "api" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == client.app.openapi(), "run: uv run python -m dclh_api.export_openapi"  # type: ignore[attr-defined]
