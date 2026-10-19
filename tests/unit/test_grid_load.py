"""`dclh load-grid`: the parsed OSM grid lands in PostGIS and in the map views (needs PostGIS)."""

from __future__ import annotations

from datetime import UTC, datetime

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point
from sqlalchemy import text

from dclh_pipeline.db import session_scope
from dclh_pipeline.grid_load import load_grid

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

AS_OF = datetime(2026, 10, 12, tzinfo=UTC)


def table(rows: list[tuple[str, int, str, str, str, object]]) -> gpd.GeoDataFrame:
    """Rows of (kind, osm_id, name, operator, voltage, geometry), as the OSM parser returns them."""
    return gpd.GeoDataFrame(
        {
            "kind": [r[0] for r in rows],
            "osm_id": [r[1] for r in rows],
            "name": [r[2] for r in rows],
            "operator": [r[3] for r in rows],
            "voltage": [r[4] for r in rows],
        },
        geometry=[r[5] for r in rows],
        crs="EPSG:2180",
    )


def line(x: float) -> LineString:
    return LineString([(x, 500_000), (x + 1000, 500_000)])


GRID = [
    ("substation", 1, "Hillwood", "Enea Operator", "110000;15000", Point(500_000, 500_000)),
    ("substation", 2, "Węzeł", "PSE", "400000;110000", Point(510_000, 500_000)),
    ("substation", 3, "", "KGHM", "", Point(520_000, 500_000)),
    ("substation", 4, "Trafo", "Tauron", "15000", Point(530_000, 500_000)),  # MV only: not loaded
    ("substation", 5, "Rozdz.", "Energa", "110000", Point(540_000, 500_000)),  # 110 kV only: not loaded
    ("line", 10, "", "", "110000", line(500_000)),
    ("line", 11, "", "", "400000;220000", line(510_000)),
    ("line", 12, "", "", "15000", line(520_000)),  # below 110 kV
    ("line", 13, "", "", "", line(530_000)),  # no voltage
]


def count(session, sql: str) -> int:  # type: ignore[no-untyped-def]
    return int(session.execute(text(sql)).scalar_one())


def test_loads_stations_and_lines_the_map_needs() -> None:
    with session_scope() as session:
        report = load_grid(session, table(GRID), as_of=AS_OF)
    assert dict(report.substations) == {"GPZ_110_SN": 1, "NODE_220_400": 1, "UNKNOWN_VOLTAGE": 1}
    assert (report.lines, report.skipped_substations, report.skipped_lines) == (2, 2, 2)
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT osm_id, name, dso, foreign_operator, cls, confidence, ST_SRID(geom), as_of "
                "FROM substations ORDER BY osm_id"
            )
        ).all()
        # what Martin serves
        assert count(session, "SELECT count(*) FROM tiles.substations") == 3
        assert count(session, "SELECT count(*) FROM tiles.lines") == 2
    assert rows[0] == (1, "Hillwood", "enea", False, "GPZ_110_SN", "estimated", 2180, AS_OF)
    assert rows[1][2:5] == ("pse", False, "NODE_220_400")
    assert rows[2][2:6] == (None, True, "UNKNOWN_VOLTAGE", "missing")  # named industrial owner, no voltage


def test_reload_replaces_rather_than_duplicates() -> None:
    with session_scope() as session:
        load_grid(session, table(GRID), as_of=AS_OF)
        load_grid(session, table(GRID[:1]), as_of=AS_OF)
    with session_scope() as session:
        assert count(session, "SELECT count(*) FROM substations") == 1
        assert count(session, "SELECT count(*) FROM lines") == 0


def test_failed_load_keeps_the_previous_grid() -> None:
    with session_scope() as session:
        load_grid(session, table(GRID), as_of=AS_OF)
    duplicate_id = [GRID[0], GRID[0]]  # primary key violation part-way through
    with pytest.raises(Exception, match="duplicate key"), session_scope() as session:
        load_grid(session, table(duplicate_id), as_of=AS_OF)
    with session_scope() as session:
        assert count(session, "SELECT count(*) FROM substations") == 3
        assert count(session, "SELECT count(*) FROM lines") == 2
