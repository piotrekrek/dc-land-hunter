"""API tests without a database: the app must degrade, not crash."""

from __future__ import annotations

from collections.abc import Iterator

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
