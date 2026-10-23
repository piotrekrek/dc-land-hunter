"""Data coverage: the area assembly, the endpoint without a database, and the endpoint on PostGIS."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from dclh_api.deps import db_session
from dclh_api.main import create_app
from dclh_api.routers.coverage import TRACKED_ITEMS, ItemKey, build_areas
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow

UNREACHABLE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none?connect_timeout=1"


def site_row(teryt: str, name: str, sites: int) -> dict[str, Any]:
    return {"teryt": teryt, "name": name, "sites": sites}


def item_row(teryt: str, key: str | None, with_data: int, entries: int, known: int) -> dict[str, Any]:
    return {"teryt": teryt, "key": key, "with_data": with_data, "entries": entries, "known": known}


def test_the_query_literal_lists_the_tracked_items_and_all() -> None:
    assert set(get_args(ItemKey)) == {"all", *TRACKED_ITEMS}


def test_share_for_one_item_counts_sites() -> None:
    areas = build_areas(
        [site_row("1421", "powiat pruszkowski", 4)],
        [
            item_row("1421", "zoning", with_data=1, entries=4, known=1),
            item_row("1421", "pen_soil_iii", 0, 4, 0),
        ],
        "zoning",
    )
    (area,) = areas
    assert (area.sites, area.with_data, area.total, area.share) == (4, 1, 4, 0.25)
    assert area.by_item == {"zoning": 0.25, "pen_soil_iii": 0.0, "bonus_psi": 0.0, "grid_headroom": 0.0}


def test_all_items_counts_score_entries_not_sites() -> None:
    areas = build_areas(
        [site_row("1421", "powiat pruszkowski", 2)],
        [
            item_row("1421", "zoning", with_data=2, entries=2, known=2),
            item_row("1421", "pen_soil_iii", with_data=0, entries=2, known=1),
        ],
        "all",
    )
    (area,) = areas
    assert (area.with_data, area.total) == (3, 4)
    assert area.share == 0.75


def test_a_null_item_key_is_a_site_without_items_and_counts_as_no_data() -> None:
    areas = build_areas([site_row("1421", "x", 1)], [item_row("1421", None, 0, 0, 0)], "all")
    (area,) = areas
    assert (area.with_data, area.total, area.share) == (0, 0, 0.0)


def test_areas_come_sorted_and_carry_geometry_when_given() -> None:
    shape = {"type": "Polygon", "coordinates": []}
    areas = build_areas(
        [site_row("1425", "b", 1), site_row("1421", "a", 1)],
        [item_row("1421", "zoning", 1, 1, 1)],
        "zoning",
        {"1421": shape},
    )
    assert [a.teryt for a in areas] == ["1421", "1425"]
    assert areas[0].geometry == shape
    assert areas[1].geometry is None


@pytest.fixture
def unreachable_client() -> Iterator[TestClient]:
    app = create_app()
    engine = create_engine(UNREACHABLE)

    def no_db() -> Iterator[Session]:
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_session] = no_db
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="member")
    with TestClient(app) as c:
        yield c


def test_coverage_without_a_database_is_a_polish_503(unreachable_client: TestClient) -> None:
    response = unreachable_client.get("/api/coverage")
    assert response.status_code == 503
    assert response.json()["detail"] == "Baza danych jest niedostępna."


@pytest.mark.parametrize(
    "query",
    ["level=kraj", "item=pen_soil_i", "item=bonus_res"],
    ids=["unknown-level", "unknown-item", "untracked-item"],
)
def test_coverage_rejects_unknown_level_and_item_before_the_database(
    unreachable_client: TestClient, query: str
) -> None:
    assert unreachable_client.get(f"/api/coverage?{query}").status_code == 422


# --- PostGIS: a small fixture of two powiats, three gminas and four sites (one excluded) ---------------------


def _items(**confidences: str) -> list[dict[str, str]]:
    return [{"key": key, "confidence": conf} for key, conf in confidences.items()]


SITES = [
    # site_id, x, y, excluded, items
    ("site-a", 10_000, 10_000, False, _items(zoning="verified", pen_soil_iii="missing", bonus_psi="estimated", grid_headroom="missing")),
    ("site-b", 30_000, 10_000, False, _items(zoning="missing", pen_soil_iii="estimated", bonus_psi="missing", grid_headroom="verified")),
    ("site-c", 60_000, 10_000, False, _items(zoning="missing", pen_soil_iii="missing", bonus_psi="estimated", grid_headroom="missing")),
    ("site-d", 10_000, 50_000, True, _items(zoning="verified", pen_soil_iii="verified", bonus_psi="verified", grid_headroom="verified")),
]  # fmt: skip

# (teryt, level, name, parent, x0, y0, x1, y1) in EPSG:2180 metres
UNITS = [
    ("14", "voivodeship", "mazowieckie", None, 0, 0, 100_000, 100_000),
    ("1421", "powiat", "powiat pruszkowski", "14", 0, 0, 50_000, 100_000),
    ("1425", "powiat", "powiat sochaczewski", "14", 50_000, 0, 100_000, 100_000),
    ("1421011", "gmina", "Piaseczno", "1421", 0, 0, 25_000, 100_000),
    ("1421022", "gmina", "Nadarzyn", "1421", 25_000, 0, 50_000, 100_000),
    ("1425011", "gmina", "Sochaczew", "1425", 50_000, 0, 100_000, 100_000),
]


@pytest.fixture
def coverage_rows() -> Iterator[None]:
    with session_scope() as session:
        for teryt, level, name, parent, x0, y0, x1, y1 in UNITS:
            session.execute(
                text(
                    "INSERT INTO admin_units (teryt, level, name, parent_teryt, geom) VALUES (:teryt, :level, :name, :parent,"
                    " ST_Multi(ST_MakeEnvelope(:x0, :y0, :x1, :y1, 2180)))"
                ),
                {
                    "teryt": teryt,
                    "level": level,
                    "name": name,
                    "parent": parent,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                },
            )
        for site_id, x, y, excluded, items in SITES:
            session.execute(
                text(
                    "INSERT INTO sites (site_id, powiat, parcel_ids, area_ha, compactness, estimated_mw, score,"
                    " upside_score, completeness, excluded, grid_badge, large_campus, split_suggested, detail,"
                    " scored_at, geom) VALUES (:id, '1421', '{}', 10, 0.5, 60, 50, 60, 0.5, :excluded, 'Brak danych',"
                    " false, false, CAST(:detail AS jsonb), now(),"
                    " ST_MakeEnvelope(:x0, :y0, :x1, :y1, 2180))"
                ),
                {
                    "id": site_id,
                    "excluded": excluded,
                    "detail": json.dumps({"score": {"items": items}, "features": {}}),
                    "x0": x - 50,
                    "y0": y - 50,
                    "x1": x + 50,
                    "y1": y + 50,
                },
            )
    yield
    with session_scope() as session:
        session.execute(text("DELETE FROM sites"))
        session.execute(text("DELETE FROM admin_units"))


@pytest.fixture
def db_client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_user] = lambda: UserRow(email="t@example.org", role="member")
    with TestClient(app) as c:
        yield c


@pytest.mark.db
@pytest.mark.usefixtures("migrated_db", "coverage_rows")
def test_powiat_coverage_counts_kept_sites_only(db_client: TestClient) -> None:
    body = db_client.get("/api/coverage?level=powiat").json()
    areas = {a["teryt"]: a for a in body["areas"]}
    assert set(areas) == {"1421", "1425"}
    powiat = areas["1421"]
    assert powiat["sites"] == 2  # site-d lies in 1421 but is excluded, so it is not counted
    assert powiat["by_item"] == {"zoning": 0.5, "pen_soil_iii": 0.5, "bonus_psi": 0.5, "grid_headroom": 0.5}
    # Overall: 2 sites x 4 items = 8 entries, 4 with data.
    assert (powiat["with_data"], powiat["total"], powiat["share"]) == (4, 8, 0.5)
    assert areas["1425"]["by_item"]["bonus_psi"] == 1.0
    assert areas["1425"]["share"] == 0.25


@pytest.mark.db
@pytest.mark.usefixtures("migrated_db", "coverage_rows")
def test_gmina_coverage_follows_the_requested_item(db_client: TestClient) -> None:
    body = db_client.get("/api/coverage?level=gmina&item=pen_soil_iii").json()
    areas = {a["teryt"]: a for a in body["areas"]}
    assert (areas["1421011"]["with_data"], areas["1421011"]["total"], areas["1421011"]["share"]) == (
        0,
        1,
        0.0,
    )
    assert (areas["1421022"]["with_data"], areas["1421022"]["total"], areas["1421022"]["share"]) == (
        1,
        1,
        1.0,
    )
    assert body["item"] == "pen_soil_iii"


@pytest.mark.db
@pytest.mark.usefixtures("migrated_db", "coverage_rows")
def test_voivodeship_level_and_geometry_on_request(db_client: TestClient) -> None:
    body = db_client.get("/api/coverage?level=wojewodztwo&item=zoning&include_geometry=true").json()
    (area,) = body["areas"]
    assert area["teryt"] == "14"
    assert (area["sites"], area["with_data"], area["total"]) == (3, 1, 3)
    assert area["geometry"]["type"] in {"Polygon", "MultiPolygon"}


@pytest.mark.db
@pytest.mark.usefixtures("migrated_db", "coverage_rows")
def test_geometry_is_left_out_unless_asked_for(db_client: TestClient) -> None:
    areas = db_client.get("/api/coverage?level=gmina").json()["areas"]
    assert areas and all(a["geometry"] is None for a in areas)
