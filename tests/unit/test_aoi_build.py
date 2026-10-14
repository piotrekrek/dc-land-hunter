"""`dclh aoi build`: the Vistula main stem, the AOI geometry on disk and the voivodeship check."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import osmium
import pytest
from alembic import command
from alembic.config import Config
from osmium.osm.mutable import Node, Way
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from dclh_pipeline import aoi_build
from dclh_pipeline.db import session_scope
from dclh_pipeline.model.aoi import lonlat_to_2180
from dclh_pipeline.settings import get_settings

ALEMBIC_INI = Path(__file__).parents[2] / "api" / "alembic.ini"


def _line(*pts: tuple[float, float]) -> LineString:
    return LineString(pts)


# Main stem 1-2-3-4 along x. Node 2-5-3 is a longer parallel channel (a loop), 3-6 a stub, and the shuffled order
# and reversed direction show that the input order does not matter.
MAIN_COORDS = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]


def _sample_ways() -> list[aoi_build.Way]:
    return [
        (3, 4, _line((20, 0), (30, 0))),
        (3, 6, _line((20, 0), (22, 0))),  # stub
        (3, 2, _line((20, 0), (10, 0))),  # main segment, reversed
        (2, 5, _line((10, 0), (15, -20))),  # parallel channel, half of the loop
        (5, 3, _line((15, -20), (20, 0))),  # parallel channel, longer than the segment 2-3
        (1, 2, _line((0, 0), (10, 0))),
    ]


def test_main_stem_takes_the_path_between_the_farthest_ends() -> None:
    stem = aoi_build.main_stem(_sample_ways())
    assert list(stem.coords) in (MAIN_COORDS, MAIN_COORDS[::-1])  # either direction is the same stem


def test_main_stem_rejects_ways_that_join_nothing() -> None:
    with pytest.raises(ValueError, match="free ends"):
        aoi_build.main_stem([(7, 7, _line((0, 0), (1, 1)))])


def test_as_multipolygon_wraps_a_single_polygon() -> None:
    square = box(0, 0, 1, 1)
    assert aoi_build.as_multipolygon(square).equals(MultiPolygon([square]))
    assert aoi_build.as_multipolygon(MultiPolygon([square])).equals(MultiPolygon([square]))
    with pytest.raises(ValueError, match="expected polygons"):
        aoi_build.as_multipolygon(_line((0, 0), (1, 1)))


def test_voivodeship_shares_report_how_much_of_each_unit_is_inside() -> None:
    prg = gpd.GeoDataFrame(
        {
            "level": ["voivodeship", "voivodeship", "powiat"],
            "name": ["west", "east", "ignored"],
        },
        geometry=[box(0, 0, 2, 1), box(2, 0, 4, 1), box(0, 0, 4, 1)],
        crs=2180,
    )
    aoi = box(0, 0, 3, 1)  # all of "west", half of "east"
    shares = aoi_build.voivodeship_shares(aoi, prg)
    assert shares == [("west", pytest.approx(1.0)), ("east", pytest.approx(0.5))]


def _write_osm(path: Path) -> Path:
    with osmium.SimpleWriter(str(path)) as w:
        # Node coordinates (lon, lat) for the Vistula stem, a stream and a side channel.
        for node_id, lon, lat in [
            (1, 19.0, 52.0),
            (2, 19.0, 52.5),
            (3, 19.0, 53.0),
            (4, 19.5, 53.0),
            (5, 19.2, 52.8),
            (6, 19.3, 52.9),
            (7, 18.0, 51.0),
            (8, 18.1, 51.1),
        ]:
            w.add_node(Node(id=node_id, location=(lon, lat)))
        # Stem as three ways in shuffled order, the middle one reversed.
        w.add_way(Way(id=10, nodes=[3, 4], tags={"waterway": "river", "name": "Wisła"}))
        w.add_way(Way(id=11, nodes=[2, 1], tags={"waterway": "river", "name": "Wisła"}))
        w.add_way(Way(id=12, nodes=[2, 3], tags={"waterway": "river", "name": "Wisła"}))
        # Not the stem: a stream with the same name, and a river with another name.
        w.add_way(Way(id=13, nodes=[7, 8], tags={"waterway": "stream", "name": "Wisła"}))
        w.add_way(Way(id=14, nodes=[5, 6], tags={"waterway": "river", "name": "Martwa Wisła"}))
    return path


def test_read_vistula_reads_only_the_named_river_from_the_extract(tmp_path: Path) -> None:
    pbf = _write_osm(tmp_path / "mini.osm.pbf")
    stem = aoi_build.read_vistula(pbf)
    # The stem runs through nodes 1 -> 2 -> 3 -> 4 (either direction) and the Martwa Wisła is left out.
    expected = [
        lonlat_to_2180(lon, lat) for lon, lat in [(19.0, 52.0), (19.0, 52.5), (19.0, 53.0), (19.5, 53.0)]
    ]
    coords = [(round(x, 3), round(y, 3)) for x, y in stem.coords]
    want = [(round(p.x, 3), round(p.y, 3)) for p in expected]
    assert coords in (want, want[::-1])


def test_read_vistula_fails_when_the_extract_has_no_such_river(tmp_path: Path) -> None:
    path = tmp_path / "empty.osm.pbf"
    with osmium.SimpleWriter(str(path)) as w:
        w.add_node(Node(id=1, location=(19.0, 52.0)))
    with pytest.raises(ValueError, match="no waterway=river"):
        aoi_build.read_vistula(path)


def test_write_geojson_stores_wgs84_and_reads_back(tmp_path: Path) -> None:
    aoi = MultiPolygon([box(600_000, 300_000, 650_000, 350_000)])
    out = tmp_path / "aoi.geojson"
    aoi_build.write_geojson(aoi, out)
    back = gpd.read_file(out)
    assert back.crs.to_epsg() == 4326
    assert back.to_crs(2180).geometry.iloc[0].area == pytest.approx(aoi.area, rel=1e-6)


@pytest.fixture(scope="module")
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


@pytest.mark.db
def test_load_aoi_replaces_the_single_row(migrated_db: None) -> None:
    first = MultiPolygon([box(600_000, 300_000, 650_000, 350_000)])
    second = MultiPolygon([Polygon([(0, 0), (1000, 0), (1000, 1000)]), box(5000, 5000, 6000, 6000)])
    with session_scope() as session:
        aoi_build.load_aoi(session, first, note="first")
    with session_scope() as session:
        aoi_build.load_aoi(session, second, note="second")
    with session_scope() as session:
        rows = session.execute(text("SELECT note, ST_SRID(geom), GeometryType(geom) FROM aoi")).all()
    assert rows == [("second", 2180, "MULTIPOLYGON")]
