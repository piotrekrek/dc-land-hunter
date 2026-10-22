"""OSM industrial and brownfield areas (powered-brownfield input) on a tiny generated PBF: no network."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import osmium
import pytest
from osmium.osm.mutable import Node, Relation, Way
from pyproj import Transformer
from shapely.geometry import Point

from dclh_pipeline.model.features import Substation, build_grid_index, powered_brownfield, zoning_fact
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.scoring.types import ZoningKind
from dclh_pipeline.sources import osm_industrial
from dclh_pipeline.sources.osm_industrial import industrial_layer, read_industrial

LAT = 52.40  # near Poznań
M_PER_DEG_LAT = 111_320
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT))
TO_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)


def _ll(x_m: float, y_m: float) -> tuple[float, float]:
    return 16.90 + x_m / M_PER_DEG_LON, LAT + y_m / M_PER_DEG_LAT


def _square(x0: float, y0: float, size: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]


def _write_pbf(path: Path) -> Path:
    """Squares 500 m apart (metres east/north of 16.90 E, 52.40 N), each its own nodes.

    w1 industrial way, w2 brownfield way, w3 residential way (dropped), w4 an open industrial way (not an area),
    r10 an industrial multipolygon from two untagged half-rings (w11 + w12) with a hole (w13), w20 an industrial
    way 50 km east (outside a powiat box).
    """
    node_id = 100
    nodes: list[Node] = []
    ways: list[Way] = []

    def ring(
        way_id: int, corners: list[tuple[float, float]], tags: dict[str, str], closed: bool = True
    ) -> None:
        nonlocal node_id
        ids = []
        for x, y in corners:
            nodes.append(Node(id=node_id, location=_ll(x, y)))
            ids.append(node_id)
            node_id += 1
        ways.append(Way(id=way_id, nodes=[*ids, ids[0]] if closed else ids, tags=tags))

    ring(1, _square(0, 0, 300), {"landuse": "industrial", "name": "Huta"})
    ring(2, _square(500, 0, 300), {"landuse": "brownfield"})
    ring(3, _square(1_000, 0, 300), {"landuse": "residential"})
    ring(4, _square(1_500, 0, 300), {"landuse": "industrial"}, closed=False)
    # r10: outer ring 0..400 split in two ways sharing their end nodes, hole 150..250.
    base = node_id
    for i, (x, y) in enumerate(_square(0, 1_000, 400)):
        nodes.append(Node(id=base + i, location=_ll(x, y)))
    node_id += 4
    ways.append(Way(id=11, nodes=[base, base + 1, base + 2]))
    ways.append(Way(id=12, nodes=[base + 2, base + 3, base]))
    ring(13, _square(150, 1_150, 100), {})
    ring(20, _square(50_000, 0, 300), {"landuse": "industrial"})
    relation = Relation(
        id=10,
        members=[("w", 11, "outer"), ("w", 12, "outer"), ("w", 13, "inner")],
        tags={"type": "multipolygon", "landuse": "industrial", "name": "Zakłady Chemiczne"},
    )
    with osmium.SimpleWriter(str(path)) as w:
        for node in sorted(nodes, key=lambda n: n.id):
            w.add_node(node)
        for way in sorted(ways, key=lambda wy: wy.id):
            w.add_way(way)
        w.add_relation(relation)
    return path


def _xy(x_m: float, y_m: float) -> tuple[float, float]:
    return TO_2180.transform(*_ll(x_m, y_m))


@pytest.fixture
def pbf(tmp_path: Path) -> Path:
    return _write_pbf(tmp_path / "mini.osm.pbf")


def test_reads_closed_ways_and_multipolygon_relations_only(pbf: Path) -> None:
    table = read_industrial(pbf)
    assert table is not None
    assert table.crs.to_epsg() == 2180
    assert sorted(table["osm_ref"]) == ["r10", "w1", "w2", "w20"]  # no residential, no open way
    named = table.set_index("osm_ref")
    assert named.loc["w2", "landuse"] == "brownfield"
    assert named.loc["r10", "name"] == "Zakłady Chemiczne"
    plant = named.loc["r10", "geometry"]
    assert plant.area == pytest.approx(400 * 400 - 100 * 100, rel=0.02)  # the hole is cut out
    assert not plant.contains(Point(*_xy(200, 1_200)))  # inside the hole
    assert plant.contains(Point(*_xy(50, 1_050)))


def test_bounds_keep_only_areas_reaching_into_the_box(pbf: Path) -> None:
    x0, y0 = _xy(-100, -100)
    x1, y1 = _xy(2_000, 2_000)
    table = read_industrial(pbf, bounds=(x0, y0, x1, y1))
    assert table is not None
    assert "w20" not in set(table["osm_ref"])
    assert len(table) == 3


def test_the_layer_is_estimated_and_feeds_powered_brownfield_and_zoning(pbf: Path) -> None:
    layer = industrial_layer(read_industrial(pbf), as_of=None)
    assert layer is not None
    assert layer.source_id == "osm_power"
    assert layer.confidence == "estimated"
    own = Substation(
        1,
        SubstationClass.GPZ_110_SN,
        (110_000, 15_000),
        Point(*_xy(200, 1_050)),
        operator="Zakłady Chemiczne",
    )
    grid = build_grid_index([own], [])
    site_near_plant = Point(*_xy(200, 1_550)).buffer(100)  # 50 m north of r10
    site_far = Point(*_xy(5_000, 5_000)).buffer(100)
    near = powered_brownfield(site_near_plant, layer, grid)
    far = powered_brownfield(site_far, layer, grid)
    assert near is not None and near.value is True
    assert far is not None and far.value is False
    on_huta = Point(*_xy(150, 150)).buffer(50)
    zoning = zoning_fact(on_huta, [], layer)
    assert zoning is not None and zoning.value is ZoningKind.UNKNOWN
    assert zoning.confidence == "estimated"
    assert zoning_fact(site_far, [], layer) is None


def test_no_layer_when_the_full_extract_is_too_large_to_read(
    pbf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dclh_pipeline.sources import osm_power

    monkeypatch.setattr(osm_power.shutil, "which", lambda _name: None)  # no osmium-tool: no cut
    monkeypatch.setattr(osm_industrial, "MAX_UNCUT_MB", 0)
    assert read_industrial(pbf) is None
    assert industrial_layer(None, as_of=None) is None


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool not installed")
def test_the_industrial_cut_keeps_relation_members_and_gives_the_same_table(
    pbf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dclh_pipeline.sources import osm_power

    from_cut = read_industrial(pbf)
    assert len(list(pbf.parent.glob("mini.industrial-*.osm.pbf"))) == 1
    monkeypatch.setattr(osm_power.shutil, "which", lambda _name: None)
    from_full = read_industrial(pbf)
    assert from_cut is not None and from_full is not None
    assert list(from_cut.sort_values("osm_ref").geometry.to_wkt()) == list(
        from_full.sort_values("osm_ref").geometry.to_wkt()
    )
