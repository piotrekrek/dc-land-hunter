"""OSM gas pipelines and runways (penalty proxies) on a tiny generated PBF: no network, no 2 GB extract."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import osmium
import pytest
from osmium.osm.mutable import Node, Way
from shapely.geometry import Point

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.features import constraint_facts
from dclh_pipeline.sources.osm_infra import (
    GAS_PIPELINE,
    RUNWAY,
    airport_ols_layer,
    gas_zone_layer,
    ols_radius_m,
    read_infra,
)

LAT = 52.40  # near Poznań
M_PER_DEG_LON = 111_320 * math.cos(math.radians(LAT))


def _lon(x_m: float) -> float:
    return 16.90 + x_m / M_PER_DEG_LON


def _write_pbf(path: Path) -> Path:
    """East-west ways, each on its own latitude band so they never share a node."""
    ways = [
        (1, {"man_made": "pipeline", "substance": "gas", "name": "DN500 Gaz-System"}, 0.0, 5_000.0, 0.00),
        (2, {"man_made": "pipeline", "type": "gas"}, 0.0, 5_000.0, 0.10),  # the older tag
        (3, {"man_made": "pipeline", "substance": "water"}, 0.0, 5_000.0, 0.20),
        (4, {"aeroway": "runway", "ref": "09/27"}, 0.0, 300.0, 0.30),  # a farm strip
        (5, {"aeroway": "runway", "ref": "08/26"}, 0.0, 700.0, 0.40),  # code 1
        (6, {"aeroway": "runway", "ref": "11/29", "name": "Ławica"}, 0.0, 2_500.0, 0.50),  # code 4
    ]
    with osmium.SimpleWriter(str(path)) as w:
        node_id = 100
        for way_id, tags, x0, x1, dlat in ways:
            w.add_node(Node(id=node_id, location=(_lon(x0), LAT + dlat)))
            w.add_node(Node(id=node_id + 1, location=(_lon(x1), LAT + dlat)))
            w.add_way(Way(id=way_id, nodes=[node_id, node_id + 1], tags=tags))
            node_id += 2
        # A runway mapped as an area: a closed 1,000 m × 40 m outline.
        corners = [(0.0, 0.0), (1_000.0, 0.0), (1_000.0, 40.0), (0.0, 40.0)]
        for i, (x, y) in enumerate(corners):
            w.add_node(Node(id=node_id + i, location=(_lon(x), LAT + 0.60 + y / 111_320)))
        ids = [node_id + i for i in range(4)]
        w.add_way(Way(id=7, nodes=[*ids, ids[0]], tags={"aeroway": "runway"}))
    return path


@pytest.fixture
def infra(tmp_path: Path):  # type: ignore[no-untyped-def]
    return read_infra(_write_pbf(tmp_path / "mini.osm.pbf"))


def test_reads_gas_pipelines_with_either_tag_and_all_runways(infra) -> None:  # type: ignore[no-untyped-def]
    assert sorted(infra["kind"]) == [GAS_PIPELINE] * 2 + [RUNWAY] * 4  # no water pipe
    assert infra.crs.to_epsg() == 2180
    named = infra.set_index("name")
    assert named.loc["Ławica", "length_m"] == pytest.approx(2_500, rel=0.01)
    assert named.loc["DN500 Gaz-System", "kind"] == GAS_PIPELINE


def test_a_runway_mapped_as_an_area_counts_half_its_outline(infra) -> None:  # type: ignore[no-untyped-def]
    area = infra[(infra["kind"] == RUNWAY) & (infra["name"] == "")]
    assert area["length_m"].iloc[0] == pytest.approx(area.geometry.iloc[0].length / 2)
    assert area["length_m"].iloc[0] == pytest.approx(1_040, rel=0.02)  # (2 × 1,000 + 2 × 40) / 2


@pytest.mark.parametrize(
    ("length_m", "radius_m"),
    [(400, 2_000), (799, 2_000), (800, 2_500), (1_199, 2_500), (1_200, 4_000), (3_000, 4_000)],
)
def test_ols_radius_follows_annex_14_by_runway_length(length_m: float, radius_m: float) -> None:
    assert ols_radius_m(length_m) == radius_m


def _site_beside(infra, name: str, gap_m: float):  # type: ignore[no-untyped-def]
    """A 10 m disc whose edge is `gap_m` north of the middle of the named way."""
    mid = infra.set_index("name").loc[name, "geometry"].interpolate(0.5, normalized=True)
    return Point(mid.x, mid.y + gap_m + 10).buffer(10)


def test_a_site_next_to_a_gas_pipeline_is_in_its_zone_and_one_further_away_is_not(infra) -> None:  # type: ignore[no-untyped-def]
    layer = gas_zone_layer(infra, zone_m=15, as_of=None)
    near = constraint_facts(_site_beside(infra, "DN500 Gaz-System", 10), {"gas_pipeline_zone": layer})
    far = constraint_facts(_site_beside(infra, "DN500 Gaz-System", 30), {"gas_pipeline_zone": layer})
    assert near["gas_pipeline_zone"] is not None and near["gas_pipeline_zone"].value is True
    assert far["gas_pipeline_zone"] is not None and far["gas_pipeline_zone"].value is False
    assert near["gas_pipeline_zone"].confidence == "estimated"
    assert near["gas_pipeline_zone"].source_id == "osm_power"


def test_ols_radius_depends_on_the_runway_and_farm_strips_are_skipped(infra) -> None:  # type: ignore[no-untyped-def]
    layer = airport_ols_layer(infra, runway_min_m=400, as_of=None)
    assert len(layer.index) == 3  # 700 m, 2,500 m and the area; not the 300 m strip

    def hit(name: str, gap_m: float) -> bool:
        fact = constraint_facts(_site_beside(infra, name, gap_m), {"airport_ols": layer})["airport_ols"]
        assert fact is not None
        return bool(fact.value)

    assert hit("Ławica", 3_000)  # code 4: 4,000 m
    assert not hit("08/26", 3_000)  # code 1: 2,000 m
    assert hit("08/26", 1_500)
    assert not hit("09/27", 100)  # the farm strip has no OLS at all


def test_the_proxy_settings_come_from_scoring_yaml() -> None:
    proxies = load_scoring().proxies
    assert proxies.gas_zone_m == 15
    assert proxies.runway_min_m == 400


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool not installed")
def test_the_infra_cut_gives_the_same_table_and_its_own_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dclh_pipeline.sources import osm_power

    pbf = _write_pbf(tmp_path / "mini.osm.pbf")
    from_cut = read_infra(pbf)
    assert len(list(tmp_path.glob("mini.infra-*.osm.pbf"))) == 1  # its own cache, next to the power cut
    monkeypatch.setattr(osm_power.shutil, "which", lambda _name: None)
    from_full = read_infra(pbf)
    assert list(from_cut.sort_values("osm_id").geometry.to_wkt()) == list(
        from_full.sort_values("osm_id").geometry.to_wkt()
    )
