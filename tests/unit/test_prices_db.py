"""RCN price comparables and the asking price against a real PostGIS database (CI and local with PostGIS)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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

_INSERT = text(
    "INSERT INTO land_transactions (tx_id, powiat, tx_date, price_zl, area_ha, land_use, nier_rodzaj,"
    " trans_type, share, parcels, geom) VALUES (:id, :powiat, :d, :price, 1, :use,"
    " 'nieruchomoscGruntowaNiezabudowana', 'wolnyRynek', '1/1', 1, ST_SetSRID(ST_MakePoint(:x, :y), 2180))"
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="member")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def site() -> Iterator[dict[str, Any]]:
    with session_scope() as session:
        write_sites(
            session,
            score_candidates(parcel_sites(), layers(), CFG),
            scored_at=datetime(2026, 10, 10, 6, 0, tzinfo=UTC),
            powiat="1421",
        )
        row = (
            session.execute(
                text(
                    "SELECT site_id, ST_X(ST_PointOnSurface(geom)) AS x, ST_Y(ST_PointOnSurface(geom)) AS y"
                    " FROM sites ORDER BY site_id LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
    yield dict(row)
    with session_scope() as session:
        session.execute(text("DELETE FROM land_transactions"))
        session.execute(text("DELETE FROM site_asking_prices"))
        session.execute(text("DELETE FROM sites"))


def _add(site: dict[str, Any], n: int, price: float, *, dx: float = 100.0, powiat: str = "1421") -> None:
    recent = datetime.now(UTC).date() - timedelta(days=60)
    with session_scope() as session:
        for i in range(n):
            session.execute(
                _INSERT,
                {
                    "id": f"tx-{powiat}-{dx}-{price}-{i}",
                    "powiat": powiat,
                    "d": recent,
                    "price": price * 10_000,
                    "use": "gruntyRolne",
                    "x": site["x"] + dx,
                    "y": site["y"],
                },
            )


def test_no_transactions_is_missing(client: TestClient, site: dict[str, Any]) -> None:
    body = client.get(f"/api/sites/{site['site_id']}/prices").json()
    assert body["comparables"]["confidence"] == "missing"
    assert body["comparables"]["agricultural"]["count"] == 0
    assert body["asking_zl_m2"] is None and body["asking_outlier"] is False


def test_unknown_site_is_404(client: TestClient, site: dict[str, Any]) -> None:
    assert client.get("/api/sites/nope/prices").status_code == 404


def test_nearby_transactions_give_verified_statistics(client: TestClient, site: dict[str, Any]) -> None:
    _add(site, 5, 6.0)
    body = client.get(f"/api/sites/{site['site_id']}/prices").json()["comparables"]
    assert body["confidence"] == "verified" and body["widened"] is False
    assert body["agricultural"]["count"] == 5
    assert body["agricultural"]["median_zl_m2"] == pytest.approx(6.0)
    assert body["source_id"] == "rcn" and body["as_of"] is not None


def test_too_few_nearby_widens_to_the_powiat(client: TestClient, site: dict[str, Any]) -> None:
    _add(site, 2, 6.0)
    _add(site, 4, 8.0, dx=60_000)  # same powiat, far away
    body = client.get(f"/api/sites/{site['site_id']}/prices").json()["comparables"]
    assert body["widened"] is True and body["confidence"] == "estimated"
    assert body["agricultural"]["count"] == 6


def test_asking_price_roundtrip_and_outlier_flag(client: TestClient, site: dict[str, Any]) -> None:
    _add(site, 5, 6.0)
    url = f"/api/sites/{site['site_id']}/asking-price"
    ok = client.put(url, json={"zl_m2": 59.0}).json()
    assert ok["asking_zl_m2"] == 59.0 and ok["asking_outlier"] is False
    high = client.put(url, json={"zl_m2": 61.0}).json()
    assert high["asking_outlier"] is True
    assert client.get(f"/api/sites/{site['site_id']}/prices").json()["asking_zl_m2"] == 61.0
    cleared = client.put(url, json={"zl_m2": None}).json()
    assert cleared["asking_zl_m2"] is None and cleared["asking_outlier"] is False


def test_asking_price_must_be_positive_and_the_site_must_exist(
    client: TestClient, site: dict[str, Any]
) -> None:
    assert client.put(f"/api/sites/{site['site_id']}/asking-price", json={"zl_m2": 0}).status_code == 422
    assert client.put("/api/sites/nope/asking-price", json={"zl_m2": 5}).status_code == 404
