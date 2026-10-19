"""The search funnel on synthetic parcels. Coordinates are metres (EPSG:2180) near Warsaw."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, box

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.features import Line, SiteLayers, Substation, build_grid_index
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.search import grid_seed_area, run_search, seed_tiles
from dclh_pipeline.sources.egib import BBox

CFG = load_scoring()
X0, Y0 = 640_000.0, 490_000.0
AREA = box(X0 - 20_000, Y0 - 20_000, X0 + 20_000, Y0 + 20_000)


def grid():
    return build_grid_index(
        [Substation(1, SubstationClass.GPZ_110_SN, (110_000, 15_000), Point(X0 + 900, Y0 + 200))],
        [Line(10, 110_000, LineString([(X0 + 1_000, Y0 - 5_000), (X0 + 1_000, Y0 + 5_000)]))],
    )


def parcels() -> gpd.GeoDataFrame:
    rows = [
        ("near-a", box(X0, Y0, X0 + 400, Y0 + 400)),  # 16 ha, touches the next one
        ("near-b", box(X0 + 400, Y0, X0 + 800, Y0 + 400)),  # 16 ha -> one 32 ha site
        ("small", box(X0 + 2_000, Y0, X0 + 2_200, Y0 + 200)),  # 4 ha: below the minimum
        ("far", box(X0 + 15_000, Y0 + 15_000, X0 + 15_800, Y0 + 15_400)),  # 32 ha, outside every buffer
    ]
    return gpd.GeoDataFrame({"parcel_id": [r[0] for r in rows]}, geometry=[r[1] for r in rows], crs=2180)


def test_seed_is_the_union_of_prefilter_buffers_cut_to_the_area() -> None:
    seed = grid_seed_area(AREA, grid(), CFG.prefilter)
    assert seed.intersects(Point(X0 + 1_000, Y0 + 2_900))  # 2.9 km from the line
    assert not seed.intersects(Point(X0 + 15_400, Y0 + 15_200))  # far from line and GPZ
    assert seed.within(AREA)


def test_seed_is_empty_when_the_grid_is_far_from_the_area() -> None:
    elsewhere = box(X0 + 500_000, Y0, X0 + 510_000, Y0 + 10_000)
    assert grid_seed_area(elsewhere, grid(), CFG.prefilter).is_empty


def test_tiles_cover_only_the_seed_not_its_bounding_box() -> None:
    # An L shape: its bounding box is 2x2 km, the L touches 3 of the 4 tiles.
    seed = box(0, 0, 2_000, 900).union(box(0, 0, 900, 2_000))
    tiles = seed_tiles(seed)
    assert len(tiles) == 3 and (1_000.0, 1_000.0, 2_000.0, 2_000.0) not in tiles


def test_funnel_counts_and_scores_only_sites_of_minimum_area() -> None:
    requested: list[BBox] = []

    def fetch(tiles: list[BBox]) -> gpd.GeoDataFrame:
        requested.extend(tiles)
        return parcels()  # the source returns everything; the funnel must still drop the far parcel

    result = run_search(AREA, SiteLayers(grid=grid()), CFG, fetch)
    report = result.report
    assert report.tiles == len(requested) > 0
    assert report.parcels_fetched == 4 and report.parcels_in_seed == 3
    assert report.clusters == 2 and report.clusters_below_min_area == 1
    assert len(result.scored) == 1 and result.scored[0].site.area_ha == pytest.approx(32.0)
    assert result.scored[0].site.parcel_ids == ("near-a", "near-b")
    assert "seed_grid" in result.scored[0].site.flags
    assert not result.scored[0].score.excluded


def test_no_parcels_means_an_empty_result_not_an_error() -> None:
    empty = gpd.GeoDataFrame({"parcel_id": []}, geometry=[], crs=2180)
    result = run_search(AREA, SiteLayers(grid=grid()), CFG, lambda tiles: empty)
    assert result.scored == [] and result.report.parcels_fetched == 0


def test_no_seed_does_not_call_the_fetcher() -> None:
    def fetch(tiles: list[BBox]) -> gpd.GeoDataFrame:
        raise AssertionError("must not fetch without a seed")

    far = box(X0 + 500_000, Y0, X0 + 510_000, Y0 + 10_000)
    assert run_search(far, SiteLayers(grid=grid()), CFG, fetch).scored == []


def test_search_needs_the_grid() -> None:
    with pytest.raises(ValueError, match="grid"):
        run_search(AREA, SiteLayers(), CFG, lambda tiles: parcels())


def test_small_lots_do_not_chain_into_one_site() -> None:
    # A town: 400 lots of 0.04 ha in a 20 x 20 grid, plus one 16 ha field next to it.
    lots = [
        (f"lot-{i}-{j}", box(X0 + i * 20, Y0 + j * 20, X0 + i * 20 + 20, Y0 + j * 20 + 20))
        for i in range(20)
        for j in range(20)
    ]
    field = ("field", box(X0 + 400, Y0, X0 + 800, Y0 + 400))
    rows = [*lots, field]
    frame = gpd.GeoDataFrame({"parcel_id": [r[0] for r in rows]}, geometry=[r[1] for r in rows], crs=2180)
    result = run_search(AREA, SiteLayers(grid=grid()), CFG, lambda tiles: frame)
    assert result.report.parcels_below_min == 400
    assert [s.site.parcel_ids for s in result.scored] == [("field",)]


def test_flood_and_housing_layers_trigger_their_hard_filters() -> None:
    # The layers `dclh search` now loads from isok and bdot10k: a flood zone over the site and a house 50 m away.
    from dclh_pipeline.model.features import GeomIndex, Layer
    from dclh_pipeline.model.provenance import Confidence

    flood = Layer("isok", GeomIndex([box(X0, Y0, X0 + 100, Y0 + 100)]), confidence=Confidence.VERIFIED)
    house = Layer(
        "bdot10k", GeomIndex([box(X0 + 850, Y0, X0 + 860, Y0 + 10)]), confidence=Confidence.VERIFIED
    )
    layers = SiteLayers(grid=grid(), residential=house, constraints={"flood_10_or_1": flood})
    result = run_search(AREA, layers, CFG, lambda tiles: parcels())
    (site,) = result.scored
    assert site.score.excluded and result.report.excluded == 1
    assert {e.key for e in site.score.exclusions} == {"flood_10_or_1_pct", "residential_within_m"}
    assert site.features.residential_min_m is not None
    assert site.features.residential_min_m.source_id == "bdot10k"
    assert site.features.residential_min_m.value == pytest.approx(50.0)


def test_loaded_layers_with_nothing_near_give_real_values_not_missing() -> None:
    from dclh_pipeline.model.features import GeomIndex, Layer

    far = box(X0 + 18_000, Y0 + 18_000, X0 + 18_010, Y0 + 18_010)
    layers = SiteLayers(
        grid=grid(),
        residential=Layer("bdot10k", GeomIndex([far])),
        constraints={"flood_10_or_1": Layer("isok", GeomIndex([]))},
    )
    (site,) = run_search(AREA, layers, CFG, lambda tiles: parcels()).scored
    assert not site.score.excluded
    assert site.features.flood_10_or_1_pct is not None and site.features.flood_10_or_1_pct.value == 0.0


def test_kimpzp_samples_only_kept_sites_and_outranks_the_register() -> None:
    from dclh_pipeline.model.features import GeomIndex, Layer, ZoningLayer
    from dclh_pipeline.model.provenance import Confidence
    from dclh_pipeline.scoring.types import ZoningKind

    pog = ZoningLayer("ru", None, ((ZoningKind.PLAN_OGOLNY_SG, box(X0 - 100, Y0 - 100, X0 + 900, Y0 + 500)),))
    asked: list[list[str]] = []

    def sample(sites):  # type: ignore[no-untyped-def]
        asked.append([s.site_id for s in sites])
        zones = tuple((ZoningKind.MPZP_PRODUCTION, s.geom) for s in sites)
        return ZoningLayer("kimpzp", None, zones, Confidence.ESTIMATED), {"plan": 3}

    result = run_search(
        AREA, SiteLayers(grid=grid(), zoning=(pog,)), CFG, lambda t: parcels(), sample_zoning=sample
    )
    (site,) = result.scored
    assert asked == [[site.site.site_id]]
    assert site.features.zoning is not None
    assert (site.features.zoning.value, site.features.zoning.source_id) == (
        ZoningKind.MPZP_PRODUCTION,
        "kimpzp",
    )
    assert result.report.kimpzp_answers == {"plan": 3}
    assert result.report.zoning == {"mpzp_production": 1}

    # An excluded site is not sampled: its zoning comes from the register alone.
    asked.clear()
    flood = Layer("isok", GeomIndex([box(X0, Y0, X0 + 100, Y0 + 100)]), confidence=Confidence.VERIFIED)
    layers = SiteLayers(grid=grid(), zoning=(pog,), constraints={"flood_10_or_1": flood})
    (excluded,) = run_search(AREA, layers, CFG, lambda t: parcels(), sample_zoning=sample).scored
    assert excluded.score.excluded and asked == []
    assert excluded.features.zoning is not None and excluded.features.zoning.source_id == "ru"
