"""OSM wind and solar clusters (`bonus_res`) on a tiny generated PBF: no network, no 2 GB extract."""

from __future__ import annotations

import math
from pathlib import Path

import osmium
import pytest
from osmium.osm.mutable import Node, Way
from shapely.geometry import Point

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.features import SiteLayers, build_site_features, res_cluster_km_fact
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.sources.osm_res import (
    SOLAR_MW_PER_HA,
    WIND_TURBINE_MW,
    parse_output_mw,
    read_res,
    res_cluster_layer,
    res_clusters,
)

LAT = 52.40  # near Poznań
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT))


def _loc(x_m: float, y_m: float) -> tuple[float, float]:
    return (16.90 + x_m / M_PER_DEG_LON, LAT + y_m / M_PER_DEG_LAT)


class _Pbf:
    def __init__(self, writer: osmium.SimpleWriter) -> None:
        self.w, self.next_id = writer, 1

    def node(self, x_m: float, y_m: float, tags: dict[str, str] | None = None) -> int:
        self.next_id += 1
        self.w.add_node(Node(id=self.next_id, location=_loc(x_m, y_m), tags=tags or {}))
        return self.next_id

    def square(self, x_m: float, y_m: float, side_m: float, tags: dict[str, str]) -> None:
        corners = [(0, 0), (side_m, 0), (side_m, side_m), (0, side_m)]
        ids = [self.node(x_m + dx, y_m + dy) for dx, dy in corners]
        self.next_id += 1
        self.w.add_way(Way(id=self.next_id, nodes=[*ids, ids[0]], tags=tags))


WIND_GEN = {"power": "generator", "generator:source": "wind"}
SOLAR_GEN = {"power": "generator", "generator:source": "solar"}


def _write_pbf(path: Path) -> Path:
    """Nodes first, then ways (PBF order). Groups are 20+ km apart, so they never merge."""
    with osmium.SimpleWriter(str(path)) as w:
        pbf = _Pbf(w)
        # A: 12 untagged turbines 400 m apart: 24 MW, a cluster.
        for i in range(12):
            pbf.node(i * 400.0, 0.0, WIND_GEN)
        # B: 5 turbines tagged 2,3 MW: 11,5 MW, too small.
        for i in range(5):
            pbf.node(i * 400.0, 20_000.0, {**WIND_GEN, "generator:output:electricity": "2,3 MW"})
        # C: rooftop PV nodes (untagged and 10 kW) and a biogas plant: never OZE cluster members.
        pbf.node(0.0, 40_000.0, SOLAR_GEN)
        pbf.node(50.0, 40_000.0, {**SOLAR_GEN, "generator:output:electricity": "10 kW"})
        pbf.node(
            100.0, 40_000.0, {"power": "plant", "plant:source": "biogas", "plant:output:electricity": "50 MW"}
        )
        # D: a 600 m × 600 m (36 ha) untagged solar farm: 28,8 MW from its area.
        pbf.square(0.0, 60_000.0, 600.0, SOLAR_GEN)
        # E: a solar plant tagged 30 MW with its untagged panel field inside: the plant counts, not both.
        pbf.square(
            0.0,
            80_000.0,
            400.0,
            {"power": "plant", "plant:source": "solar", "plant:output:electricity": "30 MWp"},
        )
        pbf.square(50.0, 80_050.0, 300.0, SOLAR_GEN)
    return path


@pytest.fixture
def objects(tmp_path: Path):  # type: ignore[no-untyped-def]
    return read_res(_write_pbf(tmp_path / "mini.osm.pbf"))


@pytest.mark.parametrize(
    ("tag", "mw"),
    [
        ("2 MW", 2.0),
        ("2,3 MW", 2.3),
        ("2.5MW", 2.5),
        ("750 kWp", 0.75),
        ("1 mw", 1.0),
        ("5000 W", 0.005),
        ("yes", None),
        ("", None),
        (None, None),
        ("2 MW;3 MW", None),
    ],
)
def test_parses_the_output_tag(tag: str | None, mw: float | None) -> None:
    assert parse_output_mw(tag) == (None if mw is None else pytest.approx(mw))


def test_reads_wind_and_solar_only_and_estimates_untagged_capacity(objects) -> None:  # type: ignore[no-untyped-def]
    assert objects.crs.to_epsg() == 2180
    assert set(objects["source"]) == {"wind", "solar"}  # no biogas
    assert len(objects) == 12 + 5 + 2 + 1 + 2
    assert sorted(set(objects.loc[objects["source"] == "wind", "mw"])) == [WIND_TURBINE_MW, 2.3]
    farm = objects[(objects["geometry"].area > 240_000) & (objects["power"] == "generator")]
    assert farm["mw"].iloc[0] == pytest.approx(36 * SOLAR_MW_PER_HA, rel=0.03)
    rooftop = objects[(objects.geom_type == "Point") & (objects["source"] == "solar")]
    assert sorted(rooftop["mw"]) == pytest.approx([0.0, 0.01])


def test_clusters_need_enough_capacity_and_a_plant_is_not_counted_twice(objects) -> None:  # type: ignore[no-untyped-def]
    clusters = res_clusters(objects).sort_values("mw")
    # A (24 MW wind), D (28,8 MW solar area), E (30 MW plant, not 30 + panels). B (11,5 MW) and C are out.
    assert list(clusters["objects"]) == [12, 1, 2]
    assert list(clusters["mw"]) == pytest.approx([24.0, 28.8, 30.0], rel=0.03)
    assert list(clusters["wind"]) == [12, 0, 0]


def test_the_link_distance_splits_or_joins_a_farm(objects) -> None:  # type: ignore[no-untyped-def]
    wind = objects[objects["source"] == "wind"]
    assert len(res_clusters(wind, link_m=300, min_cluster_mw=0)) == 17  # every turbine alone
    assert len(res_clusters(wind, link_m=500, min_cluster_mw=0)) == 2  # A and B


def test_an_empty_table_gives_no_clusters_and_a_missing_fact(objects) -> None:  # type: ignore[no-untyped-def]
    layer = res_cluster_layer(objects.iloc[0:0], as_of=None)
    assert len(layer.index) == 0
    assert res_cluster_km_fact(Point(0, 0).buffer(10), layer) is None
    assert res_cluster_km_fact(Point(0, 0).buffer(10), None) is None


def _site_near_a(objects, gap_m: float) -> Site:  # type: ignore[no-untyped-def]
    """A 10 m disc whose edge is `gap_m` south of the first turbine of A."""
    first = objects[objects["source"] == "wind"].geometry.iloc[0]
    geom = Point(first.x, first.y - gap_m - 10).buffer(10)
    return Site("s1", None, ("p1",), geom, 12.0, 0.9, 72.0, False)


def test_the_distance_feeds_the_bonus_res_item(objects) -> None:  # type: ignore[no-untyped-def]
    cfg = load_scoring()
    layers = SiteLayers(res_clusters=res_cluster_layer(objects, as_of=None))
    near = build_site_features(_site_near_a(objects, 3_000), layers, cfg)
    assert near.dist_res_cluster_km is not None
    assert near.dist_res_cluster_km.value == pytest.approx(3.0, abs=0.01)
    assert near.dist_res_cluster_km.source_id == "osm_power"
    assert near.dist_res_cluster_km.confidence == "estimated"
    far = build_site_features(_site_near_a(objects, 15_000), layers, cfg)

    def item(features):  # type: ignore[no-untyped-def]
        return next(i for i in score_site(features, cfg).items if i.key == "bonus_res")

    assert item(near).points == cfg.bonus.res_cluster_pts
    assert item(far).points == 0.0
    assert build_site_features(_site_near_a(objects, 0), SiteLayers(), cfg).dist_res_cluster_km is None
