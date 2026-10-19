"""`dclh load-admin` and `dclh grid-counts`: PRG units in PostGIS, stations counted per voivodeship (needs PostGIS)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import pytest
from alembic import command
from alembic.config import Config
from shapely.geometry import MultiPolygon, box
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from dclh_pipeline.admin_units import OUTSIDE_POLAND, load_admin_units, substation_counts
from dclh_pipeline.db import session_scope
from dclh_pipeline.settings import get_settings

pytestmark = pytest.mark.db

ALEMBIC_INI = Path(__file__).parents[2] / "api" / "alembic.ini"


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


# Two voivodeships side by side, sharing the border x = 500 000; one powiat and one gmina in the west one.
WEST = box(400_000, 400_000, 500_000, 500_000)
EAST = box(500_000, 400_000, 600_000, 500_000)


def prg(rows: list[tuple[str, str, str, object]] | None = None) -> gpd.GeoDataFrame:
    """Rows of (level, teryt, name, geometry), as `PrgConnector.parse` returns them."""
    rows = rows or [
        ("country", "0", "Polska", box(400_000, 400_000, 600_000, 500_000)),
        ("voivodeship", "14", "mazowieckie", WEST),
        ("voivodeship", "10", "łódzkie", MultiPolygon([EAST])),
        ("powiat", "1421", "pruszkowski", box(400_000, 400_000, 450_000, 450_000)),
        ("gmina", "1421011", "Piastów", box(400_000, 400_000, 410_000, 410_000)),
    ]
    return gpd.GeoDataFrame(
        {"level": [r[0] for r in rows], "teryt": [r[1] for r in rows], "name": [r[2] for r in rows]},
        geometry=[r[3] for r in rows],
        crs="EPSG:2180",
    )


def add_substation(session, osm_id: int, cls: str, dso: str | None, x: float, y: float) -> None:  # type: ignore[no-untyped-def]
    session.execute(
        text(
            "INSERT INTO substations (osm_id, dso, cls, confidence, geom) "
            "VALUES (:id, :dso, :cls, 'estimated', ST_SetSRID(ST_MakePoint(:x, :y), 2180))"
        ),
        {"id": osm_id, "dso": dso, "cls": cls, "x": x, "y": y},
    )


@pytest.fixture(autouse=True)
def empty_tables() -> None:
    with session_scope() as session:
        session.execute(text("DELETE FROM admin_units"))
        session.execute(text("DELETE FROM substations"))


def test_loads_units_with_parents_and_skips_the_country() -> None:
    with session_scope() as session:
        written = load_admin_units(session, prg())
    assert dict(written) == {"voivodeship": 2, "powiat": 1, "gmina": 1}
    with session_scope() as session:
        rows = session.execute(
            text(
                "SELECT teryt, level, name, parent_teryt, GeometryType(geom), ST_SRID(geom) "
                "FROM admin_units ORDER BY teryt"
            )
        ).all()
    assert rows == [
        ("10", "voivodeship", "łódzkie", None, "MULTIPOLYGON", 2180),
        ("14", "voivodeship", "mazowieckie", None, "MULTIPOLYGON", 2180),  # Polygon promoted
        ("1421", "powiat", "pruszkowski", "14", "MULTIPOLYGON", 2180),
        ("1421011", "gmina", "Piastów", "1421", "MULTIPOLYGON", 2180),
    ]


def test_reload_replaces_rather_than_duplicates() -> None:
    with session_scope() as session:
        load_admin_units(session, prg())
        load_admin_units(session, prg([("voivodeship", "14", "mazowieckie", WEST)]))
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM admin_units")).scalar_one() == 1


def test_bad_teryt_fails_and_keeps_the_previous_units() -> None:
    with session_scope() as session:
        load_admin_units(session, prg())
    bad = prg([("voivodeship", "14", "mazowieckie", WEST), ("powiat", "142", "obcięty", WEST)])
    with pytest.raises(ValueError, match="not 4 digits"), session_scope() as session:
        load_admin_units(session, bad)
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM admin_units")).scalar_one() == 4


def test_refuses_a_table_in_the_wrong_crs() -> None:
    with pytest.raises(ValueError, match="EPSG:2180"), session_scope() as session:
        load_admin_units(session, prg().to_crs("EPSG:4326"))


def test_counts_per_voivodeship_border_once_and_outside_poland() -> None:
    with session_scope() as session:
        load_admin_units(session, prg())
        add_substation(session, 1, "GPZ_110_SN", "pge", 420_000, 420_000)
        add_substation(session, 2, "GPZ_110_SN", None, 430_000, 420_000)
        add_substation(session, 3, "NODE_220_400", "pse", 450_000, 450_000)
        add_substation(session, 4, "GPZ_110_SN", "pge", 550_000, 450_000)
        add_substation(session, 5, "UNKNOWN_VOLTAGE", None, 500_000, 450_000)  # on the shared border
        add_substation(session, 6, "GPZ_110_SN", "energa", 700_000, 450_000)  # outside both
    with session_scope() as session:
        counts = substation_counts(session)
    assert [(c.teryt, c.name) for c in counts] == [
        ("10", "łódzkie"),
        ("14", "mazowieckie"),
        (None, OUTSIDE_POLAND),
    ]
    lodz, maz, outside = counts
    assert maz.by_class == {"GPZ_110_SN": 2, "NODE_220_400": 1}
    assert maz.by_dso == {"pge": 1, "-": 1}  # GPZs only; the node is not counted per DSO
    assert lodz.by_class == {
        "GPZ_110_SN": 1,
        "UNKNOWN_VOLTAGE": 1,
    }  # border station counted once, lowest TERYT
    assert outside.by_dso == {"energa": 1}
    assert sum(sum(c.by_class.values()) for c in counts) == 6


def test_counts_need_admin_units() -> None:
    with pytest.raises(ValueError, match="dclh load-admin"), session_scope() as session:
        substation_counts(session)


def test_station_on_the_outer_edge_counts_inside() -> None:
    # ST_Covers, not ST_Contains: a point on the boundary belongs to the voivodeship
    with session_scope() as session:
        load_admin_units(session, prg())
        add_substation(session, 7, "GPZ_110_SN", "pge", 400_000, 420_000)
    with session_scope() as session:
        (only,) = substation_counts(session)
    assert only.teryt == "14"
