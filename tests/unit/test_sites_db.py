"""The sites table and the sites API against a real PostGIS database (CI and local with PostGIS)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import score_candidates, write_sites
from dclh_pipeline.settings import get_settings
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = pytest.mark.db

ALEMBIC_INI = Path(__file__).parents[2] / "api" / "alembic.ini"
SCORED_AT = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> Iterator[None]:
    url = get_settings().database_url
    try:
        with create_engine(url, connect_args={"connect_timeout": 2}).connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip(f"no database at {url}")
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="member")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded() -> Iterator[None]:
    with session_scope() as session:
        write_sites(session, score_candidates(parcel_sites(), layers(), CFG), scored_at=SCORED_AT)
    yield


def test_run_writes_sites_and_replaces_previous_run(seeded: None) -> None:
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM sites")).scalar_one() == 3
        write_sites(session, score_candidates(parcel_sites()[:1], layers(), CFG), scored_at=SCORED_AT)
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM sites")).scalar_one() == 1


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
