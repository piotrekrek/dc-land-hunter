"""Site features from geometry. Coordinates are metres (EPSG:2180), so the expected distances are exact."""

from __future__ import annotations

import json
import math

import pytest
from shapely.geometry import LineString, Point, box
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.features import (
    GeomIndex,
    Layer,
    Line,
    SiteLayers,
    Substation,
    ZoningLayer,
    build_grid_index,
    build_site_features,
    constraint_facts,
    grid_facts,
    overlap_pct,
    passes_grid_prefilter,
    physical_lines,
    powered_brownfield,
    residential_clear_fact,
    residential_fact,
    zoning_fact,
)
from dclh_pipeline.model.grid import SubstationClass
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Parcel, Site, cluster_sites
from dclh_pipeline.scoring.run import to_jsonable
from dclh_pipeline.scoring.types import ZoningKind

CFG = load_scoring()
SITE = box(0, 0, 500, 500)  # 25 ha


def line(osm_id: int, x: float, voltage: int | None = 110_000) -> Line:
    return Line(osm_id, voltage, LineString([(x, -10_000), (x, 10_000)]))


def sub(
    osm_id: int,
    x: float,
    y: float,
    cls: SubstationClass,
    voltages: tuple[int, ...] = (110_000, 15_000),
    operator: str | None = None,
) -> Substation:
    return Substation(osm_id, cls, voltages, Point(x, y), operator=operator)


def layer(
    geoms: list[BaseGeometry], source_id: str = "test", confidence: Confidence = Confidence.ESTIMATED
) -> Layer:
    return Layer(source_id, GeomIndex(geoms), as_of=None, confidence=confidence)


# --- grid -----------------------------------------------------------------------------------------------------------


def test_distances_to_grid_classes_in_km() -> None:
    grid = build_grid_index(
        [
            sub(1, 1_000 + 2_000, 250, SubstationClass.GPZ_110_SN),  # 2.5 km east of the site's east edge
            sub(2, 500 + 30_000, 250, SubstationClass.NODE_220_400, (400_000,)),
        ],
        [line(10, 1_500)],  # 1 km east of the east edge
    )
    facts = grid_facts(SITE, grid, CFG)
    assert facts["dist_line110_km"].value == pytest.approx(1.0)  # type: ignore[union-attr]
    assert facts["dist_gpz110_km"].value == pytest.approx(2.5)  # type: ignore[union-attr]
    assert facts["dist_node_220_400_km"].value == pytest.approx(30.0)  # type: ignore[union-attr]
    assert facts["dist_line110_km"].source_id == "osm_power"  # type: ignore[union-attr]
    assert facts["dist_line110_km"].confidence is Confidence.ESTIMATED  # type: ignore[union-attr]


def test_n1_counts_distinct_110kv_lines_within_radius() -> None:
    grid = build_grid_index(
        [],
        [
            line(10, 500 + 1_000),  # within 3 km
            line(11, 500 + 2_500),  # within 3 km
            line(12, 500 + 5_000),  # outside 3 km
            line(13, 500 + 1_200, voltage=220_000),  # 220 kV is not a 110 kV line
            line(14, 500 + 1_300, voltage=None),  # unknown voltage: never counted as 110 kV
        ],
    )
    facts = grid_facts(SITE, grid, CFG)
    assert facts["independent_110_lines_near"].value == 2  # type: ignore[union-attr]
    assert facts["dist_line110_km"].value == pytest.approx(1.0)  # type: ignore[union-attr]


def seg(osm_id: int, *coords: tuple[float, float]) -> Line:
    return Line(osm_id, 110_000, LineString(coords))


def n1(lines: list[Line]) -> int:
    return grid_facts(SITE, build_grid_index([], lines), CFG)["independent_110_lines_near"].value  # type: ignore[union-attr,return-value]


def test_n1_counts_a_line_split_into_many_ways_once() -> None:
    # One physical line east of the site, cut into four ways that meet end to end (as OSM maps it).
    ys = [-5_000, -2_000, 0, 2_000, 5_000]
    ways = [seg(100 + k, (1_500, ys[k]), (1_500, ys[k + 1])) for k in range(4)]
    assert n1(ways) == 1
    assert n1([*ways, line(10, 2_500)]) == 2


def test_n1_skips_busbar_and_portal_stubs_inside_a_substation() -> None:
    # Two lines end at a GPZ 1 km east of the site; the station's busbars are mapped as 10-40 m line ways,
    # one of them touching a line's end (Rzepin, GPZ Lubiechnia Wielka: 11 ways, 4 physical lines).
    west = seg(1, (1_500, 250), (1_500, 9_000))
    east = seg(2, (1_520, 250), (9_000, 250))
    stubs = [
        seg(3, (1_500, 250), (1_500, 240)),  # continues `west`: joined, the line stays one line
        seg(4, (1_505, 230), (1_535, 230)),
        seg(5, (1_535, 230), (1_545, 230)),  # joined with 4, still 40 m in total
        seg(6, (1_510, 220), (1_510, 210)),
    ]
    assert n1([west, east, *stubs]) == 2


def test_n1_keeps_the_legs_of_a_branch_apart() -> None:
    # Three ways meet at one tower: a branch point, not a line split in two, so nothing is joined.
    t = (1_500, 250)
    assert n1([seg(1, t, (1_500, 9_000)), seg(2, t, (1_500, -9_000)), seg(3, t, (9_000, 250))]) == 3


def test_ways_ending_where_another_line_passes_are_not_joined() -> None:
    # Two ways end at a vertex in the middle of a third way: three ways touch that node.
    through = seg(1, (0, -9_000), (1_500, 250), (0, 9_000))
    a = seg(2, (1_500, 250), (9_000, 250))
    b = seg(3, (1_500, 250), (9_000, 5_000))
    assert physical_lines([(w.osm_id, w.geom) for w in (through, a, b)]) == [1, 2, 3]


def test_only_stubs_within_radius_is_a_real_zero() -> None:
    assert n1([seg(1, (1_500, 250), (1_500, 260)), line(10, 500 + 4_000)]) == 0


def test_empty_grid_layer_is_missing_not_zero() -> None:
    grid = build_grid_index([], [line(10, 1_000)])  # lines but no substations
    facts = grid_facts(SITE, grid, CFG)
    assert facts["dist_gpz110_km"] is None
    assert facts["dist_node_220_400_km"] is None
    assert facts["dist_line110_km"] is not None


def test_zero_lines_within_radius_is_a_real_zero() -> None:
    grid = build_grid_index([], [line(10, 500 + 4_000)])
    assert grid_facts(SITE, grid, CFG)["independent_110_lines_near"].value == 0  # type: ignore[union-attr]


def test_prefilter_keeps_zone_if_any_class_is_near_enough() -> None:
    prefilter = CFG.prefilter
    near_line = build_grid_index([], [line(1, 500 + 2_500)])
    near_gpz = build_grid_index([sub(1, 500 + 4_500, 250, SubstationClass.GPZ_110_SN)], [])
    near_node = build_grid_index([sub(1, 500 + 20_000, 250, SubstationClass.NODE_220_400, (400_000,))], [])
    far = build_grid_index([sub(1, 500 + 6_000, 250, SubstationClass.GPZ_110_SN)], [line(2, 500 + 3_500)])
    assert passes_grid_prefilter(SITE, near_line, prefilter)  # 2.5 km < 3 km
    assert passes_grid_prefilter(SITE, near_gpz, prefilter)  # 4.5 km < 5 km
    assert passes_grid_prefilter(SITE, near_node, prefilter)  # 20 km < 25 km
    assert not passes_grid_prefilter(SITE, far, prefilter)  # line 3.5 km, GPZ 6 km: both too far


def test_build_grid_index_classifies_by_substation_class() -> None:
    grid = build_grid_index(
        [
            sub(1, 0, 0, SubstationClass.GPZ_110_SN),
            sub(
                2, 0, 0, SubstationClass.OTHER, (110_000,)
            ),  # 110 kV only: not a GPZ, still a 110 kV substation
            sub(3, 0, 0, SubstationClass.OTHER, (15_000,)),  # MV only
        ],
        [],
    )
    assert len(grid.gpz110) == 1
    assert len(grid.hv110_substations) == 2


# --- powered brownfields --------------------------------------------------------------------------------------------


def test_powered_brownfield_needs_110kv_substation_in_nearby_industrial_area() -> None:
    industrial_next_door = layer([box(600, 0, 1_200, 400)])  # 100 m east of the site
    steelworks = "Huta Zawiercie S.A."
    grid_with_station = build_grid_index(
        [sub(1, 900, 200, SubstationClass.GPZ_110_SN, operator=steelworks)], []
    )
    grid_without = build_grid_index([sub(1, 5_000, 200, SubstationClass.GPZ_110_SN, operator=steelworks)], [])
    assert powered_brownfield(SITE, industrial_next_door, grid_with_station).value is True  # type: ignore[union-attr]
    assert powered_brownfield(SITE, industrial_next_door, grid_without).value is False  # type: ignore[union-attr]


def test_utility_and_unlabelled_substations_do_not_make_a_powered_brownfield() -> None:
    industrial_next_door = layer([box(600, 0, 1_200, 400)])
    for operator in ("Enea Operator S.A.", "PSE S.A.", "Tauron Dystrybucja", None):
        grid = build_grid_index([sub(1, 900, 200, SubstationClass.GPZ_110_SN, operator=operator)], [])
        fact = powered_brownfield(SITE, industrial_next_door, grid)
        assert fact is not None
        assert fact.value is False, operator


def test_powered_brownfield_is_missing_without_industrial_layer() -> None:
    grid = build_grid_index([sub(1, 0, 0, SubstationClass.GPZ_110_SN)], [])
    assert powered_brownfield(SITE, None, grid) is None


def test_industrial_area_far_away_is_not_a_brownfield() -> None:
    far_area = layer([box(2_000, 0, 2_400, 400)])
    grid = build_grid_index([sub(1, 2_100, 200, SubstationClass.GPZ_110_SN)], [])
    assert powered_brownfield(SITE, far_area, grid).value is False  # type: ignore[union-attr]


# --- zoning ---------------------------------------------------------------------------------------------------------


def test_zoning_takes_the_zone_covering_most_of_the_site() -> None:
    zones = ZoningLayer(
        "ru",
        None,
        (
            (ZoningKind.OTHER, box(-100, -100, 100, 600)),  # small overlap
            (ZoningKind.MPZP_PRODUCTION, box(50, -100, 600, 600)),  # most of the site
        ),
    )
    fact = zoning_fact(SITE, (zones,), None)
    assert fact is not None
    assert fact.value is ZoningKind.MPZP_PRODUCTION
    assert fact.confidence is Confidence.VERIFIED
    assert fact.source_id == "ru"


def test_a_zone_that_only_touches_or_slivers_the_site_does_not_decide_the_zoning() -> None:
    production = ZoningLayer(
        "ru", None, ((ZoningKind.MPZP_PRODUCTION, box(-500, 0, 0, 400)),)
    )  # shares an edge
    touching = zoning_fact(SITE, (production,), None)
    assert touching is not None
    assert touching.value is ZoningKind.UNKNOWN
    assert touching.confidence is Confidence.MISSING
    sliver = ZoningLayer("ru", None, ((ZoningKind.MPZP_PRODUCTION, box(-500, 0, 0.5, 400)),))  # 0.5 m overlap
    assert zoning_fact(SITE, (sliver,), None).value is ZoningKind.UNKNOWN  # type: ignore[union-attr]
    covering = ZoningLayer("ru", None, ((ZoningKind.MPZP_PRODUCTION, box(-500, 0, 300, 400)),))  # most of it
    assert zoning_fact(SITE, (covering,), None).value is ZoningKind.MPZP_PRODUCTION  # type: ignore[union-attr]


def test_industrial_area_without_plan_is_unknown_not_bad() -> None:
    industrial = layer([box(400, 400, 900, 900)], source_id="osm_land")
    no_plan = ZoningLayer("ru", None, ())
    fact = zoning_fact(SITE, (no_plan,), industrial)
    assert fact is not None
    assert fact.value is ZoningKind.UNKNOWN
    assert fact.confidence is Confidence.ESTIMATED
    assert fact.source_id == "osm_land"


def test_loaded_register_without_any_plan_is_unknown_with_missing_confidence() -> None:
    fact = zoning_fact(SITE, (ZoningLayer("ru", None, ()),), None)
    assert fact is not None
    assert fact.value is ZoningKind.UNKNOWN
    assert fact.confidence is Confidence.MISSING


def test_zoning_is_missing_when_no_planning_layer_is_loaded() -> None:
    assert zoning_fact(SITE, (), None) is None


# --- constraints and residential distance ---------------------------------------------------------------------------


def test_overlap_pct_is_share_of_site_area_and_does_not_double_count() -> None:
    half = box(-100, -100, 250, 600)  # covers x 0..250 of the 500 m site: 50 %
    overlapping_copy = box(-100, -100, 250, 600)
    assert overlap_pct(SITE, [half]) == pytest.approx(50.0)
    assert overlap_pct(SITE, [half, overlapping_copy]) == pytest.approx(50.0)
    assert overlap_pct(SITE, [box(1_000, 0, 1_100, 100)]) == 0.0


def test_constraint_facts_use_their_own_source_and_confidence() -> None:
    constraints = {
        "flood_10_or_1": layer([box(-100, -100, 250, 600)], "isok", Confidence.VERIFIED),
        "heritage_register": layer([box(480, 480, 700, 700)], "nid", Confidence.VERIFIED),
        "forest": layer([box(5_000, 0, 6_000, 1_000)], "bdl"),
    }
    facts = constraint_facts(SITE, constraints)
    assert facts["flood_10_or_1_pct"].value == pytest.approx(50.0)  # type: ignore[union-attr]
    assert facts["flood_10_or_1_pct"].source_id == "isok"  # type: ignore[union-attr]
    assert facts["flood_10_or_1_pct"].confidence is Confidence.VERIFIED  # type: ignore[union-attr]
    assert facts["on_heritage_register"].value is True  # type: ignore[union-attr]
    assert facts["forest_pct"].value == 0.0  # type: ignore[union-attr]
    assert "soil_class_i_ii_pct" not in facts  # layer not loaded: missing, not zero


def test_residential_distance_in_metres() -> None:
    fact = residential_fact(SITE, layer([box(650, 0, 700, 50)], "bdot10k"))
    assert fact is not None and fact.value == pytest.approx(150.0)
    assert residential_fact(SITE, None) is None


def test_residential_clear_area_is_the_largest_part_far_enough_from_housing() -> None:
    # A house 1 m outside a corner of the 25 ha site: the site edge is 1 m away, but most of it is clear.
    corner = residential_clear_fact(SITE, layer([box(501, 501, 511, 511)], "bdot10k"), 100.0)
    assert corner is not None and 23.0 < corner.value < 25.0
    assert corner.source_id == "bdot10k"
    # A terrace across the middle (y 245..255): two 500 x 145 m strips stay clear, 7.25 ha each.
    middle = residential_clear_fact(SITE, layer([box(-10, 245, 510, 255)]), 100.0)
    assert middle is not None and middle.value == pytest.approx(7.25)
    # Nothing within the threshold: the whole site is clear.
    far = residential_clear_fact(SITE, layer([box(700, 0, 710, 10)]), 100.0)
    assert far is not None and far.value == pytest.approx(25.0)
    assert residential_clear_fact(SITE, None, 100.0) is None


# --- assembly -------------------------------------------------------------------------------------------------------


def test_the_first_layer_with_a_zone_decides_even_when_a_later_one_scores_higher() -> None:
    # An MPZP in force (KIMPZP) outranks the plan ogólny, whatever the plan ogólny says.
    mpzp = ZoningLayer("kimpzp", None, ((ZoningKind.OTHER, SITE),), Confidence.ESTIMATED)
    pog = ZoningLayer("ru_pog", None, ((ZoningKind.PLAN_OGOLNY_SG, SITE),))
    fact = zoning_fact(SITE, (mpzp, pog), None)
    assert fact is not None
    assert (fact.value, fact.source_id, fact.confidence) == (ZoningKind.OTHER, "kimpzp", Confidence.ESTIMATED)


def test_an_empty_first_layer_falls_through_to_the_next() -> None:
    pog = ZoningLayer("ru_pog", None, ((ZoningKind.PLAN_OGOLNY_SG, SITE),))
    fact = zoning_fact(SITE, (ZoningLayer("kimpzp", None, ()), pog), None)
    assert fact is not None
    assert (fact.value, fact.source_id) == (ZoningKind.PLAN_OGOLNY_SG, "ru_pog")


def test_build_site_features_from_cluster_and_layers() -> None:
    site = cluster_sites(
        [Parcel("p1", "z1", box(0, 0, 500, 500)), Parcel("p2", "z1", box(500, 0, 700, 500))],
        mw_per_ha=CFG.target.mw_per_ha,
        split_suggestion_ha=CFG.target.large_site_split_ha,
    )[0]
    grid = build_grid_index([sub(1, 1_000, 250, SubstationClass.GPZ_110_SN)], [line(10, 1_300)])
    layers = SiteLayers(
        grid=grid, zoning=(ZoningLayer("ru", None, ((ZoningKind.PLAN_OGOLNY_SG, box(-10, -10, 800, 600)),)),)
    )

    features = build_site_features(site, layers, CFG, parcels_as_of=None)

    assert features.area_ha is not None and features.area_ha.value == pytest.approx(site.area_ha)
    assert features.area_ha.source_id == "egib"
    assert features.parcel_count is not None and features.parcel_count.value == 2
    assert features.zoning is not None and features.zoning.value is ZoningKind.PLAN_OGOLNY_SG
    assert features.dist_line110_km is not None and features.dist_line110_km.value == pytest.approx(
        0.6
    )  # site spans x 0..700
    assert features.dist_node_220_400_km is None  # no node layer loaded: missing
    assert features.residential_min_m is None
    assert features.residential_clear_ha is None
    assert features.flood_10_or_1_pct is None


def test_build_site_features_with_nothing_loaded_is_only_land() -> None:
    site = cluster_sites(
        [Parcel("p1", None, box(0, 0, 500, 500))],
        mw_per_ha=CFG.target.mw_per_ha,
        split_suggestion_ha=CFG.target.large_site_split_ha,
    )[0]
    features = build_site_features(site, SiteLayers(), CFG)
    assert features.zoning is None
    assert features.dist_gpz110_km is None
    assert features.powered_brownfield is None
    assert features.area_ha is not None


def test_closer_line_never_gives_a_larger_distance() -> None:
    far = grid_facts(SITE, build_grid_index([], [line(1, 3_000)]), CFG)["dist_line110_km"]
    near = grid_facts(SITE, build_grid_index([], [line(1, 1_000)]), CFG)["dist_line110_km"]
    assert far is not None and near is not None and near.value < far.value


def test_non_finite_geometry_values_are_missing_data() -> None:
    """A NaN area or compactness must not reach the scorer as a number: it is missing, and the JSON stays valid."""
    site = Site(
        site_id="site-nan",
        zone_id="z1",
        parcel_ids=("p1",),
        geom=box(0, 0, 500, 500),
        area_ha=math.nan,
        compactness=math.nan,
        estimated_mw=math.nan,
        needs_split_suggestion=False,
    )

    features = build_site_features(site, SiteLayers(), CFG)

    assert features.area_ha is None
    assert features.compactness is None
    assert features.parcel_count is not None and features.parcel_count.value == 1
    json.dumps(to_jsonable(features), allow_nan=False)  # the sites.detail column is jsonb, which has no NaN
