"""`dclh aoi build`: the Vistula main stem, the AOI geometry on disk and the voivodeship check."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import osmium
import pytest
from osmium.osm.mutable import Node, Way
from pydantic import ValidationError
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from sqlalchemy import text

from dclh_pipeline import aoi_build
from dclh_pipeline.config import IncludedUnit, load_aoi_config
from dclh_pipeline.db import session_scope
from dclh_pipeline.model.aoi import lonlat_to_2180


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


def _prg_units() -> gpd.GeoDataFrame:
    # Two gminas with the same code digits at different levels, to check that the level is matched too.
    return gpd.GeoDataFrame(
        {
            "level": ["gmina", "gmina", "powiat"],
            "teryt": ["0463011", "0663011", "0463"],
            "name": ["Toruń", "Lublin", "toruński"],
        },
        geometry=[box(0, 0, 1, 1), box(2, 0, 3, 1), box(0, 0, 9, 9)],
        crs=2180,
    )


def test_included_geometries_match_one_unit_by_level_and_teryt() -> None:
    units = [IncludedUnit(level="gmina", teryt="0463011", name="Toruń")]
    geoms = aoi_build.included_geometries(_prg_units(), units)
    assert len(geoms) == 1 and geoms[0].equals(box(0, 0, 1, 1))


def test_included_geometries_reject_a_teryt_that_prg_does_not_have() -> None:
    units = [IncludedUnit(level="gmina", teryt="0461011", name="Toruń (miasto)")]
    with pytest.raises(ValueError, match="PRG has 0 units"):
        aoi_build.included_geometries(_prg_units(), units)


def test_included_geometries_match_a_powiat_by_its_own_level() -> None:
    units = [IncludedUnit(level="powiat", teryt="0463", name="toruński")]
    assert aoi_build.included_geometries(_prg_units(), units)[0].equals(box(0, 0, 9, 9))


def test_committed_aoi_config_adds_torun() -> None:
    config = load_aoi_config()  # config/aoi.yaml, the file the owner edits
    assert [(u.level, u.teryt) for u in config.include] == [("gmina", "0463011")]


def test_aoi_config_loads_an_empty_file_as_no_additions(tmp_path: Path) -> None:
    path = tmp_path / "aoi.yaml"
    path.write_text("")
    assert load_aoi_config(path).include == []


def test_aoi_config_rejects_a_teryt_of_the_wrong_length(tmp_path: Path) -> None:
    path = tmp_path / "aoi.yaml"
    path.write_text("include:\n  - {level: gmina, teryt: '046301', name: Toruń}\n")
    with pytest.raises(ValidationError, match="7 digits"):
        load_aoi_config(path)


def test_aoi_config_rejects_duplicate_units(tmp_path: Path) -> None:
    path = tmp_path / "aoi.yaml"
    unit = "{level: gmina, teryt: '0463011', name: Toruń}"
    path.write_text(f"include:\n  - {unit}\n  - {unit}\n")
    with pytest.raises(ValidationError, match="duplicate"):
        load_aoi_config(path)


def test_write_geojson_stores_wgs84_and_reads_back(tmp_path: Path) -> None:
    aoi = MultiPolygon([box(600_000, 300_000, 650_000, 350_000)])
    out = tmp_path / "aoi.geojson"
    aoi_build.write_geojson(aoi, out)
    back = gpd.read_file(out)
    assert back.crs.to_epsg() == 4326
    assert back.to_crs(2180).geometry.iloc[0].area == pytest.approx(aoi.area, rel=1e-6)


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
