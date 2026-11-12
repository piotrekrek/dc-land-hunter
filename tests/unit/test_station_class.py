"""Substation classification: only a DSO/PSE station is a connection point (Rzepin RTB review, spec 1)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from dclh_api.routers.coverage import build_station_classes
from dclh_pipeline.config import load_scoring
from dclh_pipeline.grid_capacity import upgrade_listed_stations
from dclh_pipeline.model.features import Line, Substation, build_grid_index, grid_facts
from dclh_pipeline.model.gpz_match import MatchResult
from dclh_pipeline.model.grid import (
    ClassSource,
    StationClass,
    SubstationClass,
    classify_station,
)
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring import Fact, SiteFeatures, score_site
from dclh_pipeline.sources.osm_power import to_grid

CFG = load_scoring()
OP, GEN, CUST, UNK = (
    StationClass.OPERATOR_GPZ,
    StationClass.GENERATOR,
    StationClass.CUSTOMER,
    StationClass.UNKNOWN,
)


@pytest.mark.parametrize(
    ("operator", "owner", "name", "substation", "cls", "source"),
    [
        # the Rzepin test case: a wind farm's collector station
        (None, None, "GPO Lubiechnia Wielka 110/20 kV", None, GEN, ClassSource.NAME_PATTERN),
        (None, None, "gpo lubiechnia wielka", None, GEN, ClassSource.NAME_PATTERN),
        ("Enea Operator", None, "GPZ Rzepin 110/15 kV", None, OP, ClassSource.OSM_OPERATOR_TAG),
        ("Enea Operator", None, "GPZ Rzepin", "distribution", OP, ClassSource.OSM_OPERATOR_TAG),
        (None, "Tauron Dystrybucja S.A.", "GPZ Piaski", None, OP, ClassSource.OSM_OPERATOR_TAG),  # owner tag
        ("PSE S.A.", None, "Plewiska", "transmission", OP, ClassSource.OSM_OPERATOR_TAG),
        ("Tauron", None, None, None, OP, ClassSource.OSM_OPERATOR_TAG),
        # the substation tag beats the operator tag
        ("Enea Operator", None, "GPZ X", "generation", GEN, ClassSource.OSM_SUBSTATION_TAG),
        (None, None, "Stacja", "Industrial", CUST, ClassSource.OSM_SUBSTATION_TAG),
        # name patterns, with diacritics and case
        (None, None, "Farma Wiatrowa Żary", None, GEN, ClassSource.NAME_PATTERN),
        (None, None, "FW Kamień 110/30", None, GEN, ClassSource.NAME_PATTERN),
        (None, None, "PV Brudzewo", None, GEN, ClassSource.NAME_PATTERN),
        (None, None, "Elektrownia Słoneczna Zagórze", None, GEN, ClassSource.NAME_PATTERN),
        (None, None, "Stacja Odbiorcza Huty", None, CUST, ClassSource.NAME_PATTERN),
        (None, None, "STACJA ODBIORCZA", None, CUST, ClassSource.NAME_PATTERN),
        # another named owner: a customer's own station
        ("KGHM Polska Miedź", None, "GPZ Głogów", None, CUST, ClassSource.OSM_OPERATOR_TAG),
        # nothing says whose it is
        (None, None, "GPZ Rzepin", None, UNK, None),
        (None, None, None, None, UNK, None),
        ("", "", "", "", UNK, None),
        (
            "PKP Energetyka",
            None,
            "Podstacja trakcyjna",
            None,
            UNK,
            None,
        ),  # rail traction: not a customer claim
        # words that merely contain a pattern are not a pattern
        (None, None, "Gpoznań", None, UNK, None),
        (None, None, "Pvc Zakład", None, UNK, None),
    ],
)
def test_classify_station(
    operator: str | None,
    owner: str | None,
    name: str | None,
    substation: str | None,
    cls: StationClass,
    source: ClassSource | None,
) -> None:
    result = classify_station(operator, owner, name, substation)
    assert (result.station_class, result.source) == (cls, source)
    assert result.confidence is (Confidence.MISSING if cls is UNK else Confidence.ESTIMATED)


def test_to_grid_carries_owner_and_substation_tags_into_the_class() -> None:
    rows = [
        ("GPO Lubiechnia Wielka 110/20 kV", "", "", ""),
        ("GPZ Rzepin 110/15 kV", "Enea Operator", "", ""),
        ("Stacja X", "", "Enea Operator", ""),
        ("Stacja Y", "Enea Operator", "", "industrial"),
    ]
    table = gpd.GeoDataFrame(
        {
            "kind": "substation",
            "osm_id": range(1, 5),
            "name": [r[0] for r in rows],
            "operator": [r[1] for r in rows],
            "owner": [r[2] for r in rows],
            "substation": [r[3] for r in rows],
            "voltage": "110000;15000",
        },
        geometry=[Point(i, 0) for i in range(4)],
        crs="EPSG:2180",
    )
    subs, _ = to_grid(table)
    assert [s.station_class for s in subs] == [GEN, OP, OP, CUST]
    assert subs[0].class_source is ClassSource.NAME_PATTERN


def test_to_grid_tolerates_a_table_without_the_new_columns() -> None:
    table = gpd.GeoDataFrame(
        {
            "kind": ["substation"],
            "osm_id": [1],
            "name": ["GPZ A"],
            "operator": ["Enea Operator"],
            "voltage": ["110000;15000"],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:2180",
    )
    (sub,), _ = to_grid(table)
    assert sub.station_class is OP


def _gpz(osm_id: int, x: float, station_class: StationClass, name: str = "") -> Substation:
    return Substation(
        osm_id,
        SubstationClass.GPZ_110_SN,
        (110_000, 15_000),
        Point(x, 0),
        name=name or None,
        station_class=station_class,
    )


def test_operator_list_match_upgrades_to_operator_gpz_verified() -> None:
    subs = [_gpz(1, 0, UNK, "Rzepin"), _gpz(2, 10, GEN, "GPO Lubiechnia Wielka"), _gpz(3, 20, UNK, "Inna")]
    results = [
        MatchResult("enea", "Rzepin", 1, 100.0, "fuzzy", ""),
        MatchResult("enea", "Nigdzie", None, 0.0, "none", ""),
    ]
    out = upgrade_listed_stations(subs, results)
    assert [(s.station_class, s.class_source, s.class_confidence) for s in out] == [
        (OP, ClassSource.OPERATOR_LIST, Confidence.VERIFIED),
        (GEN, None, Confidence.MISSING),
        (UNK, None, Confidence.MISSING),
    ]


def test_a_list_match_on_a_220_400_node_is_not_a_gpz_upgrade() -> None:
    node = Substation(9, SubstationClass.NODE_220_400, (400_000,), Point(0, 0))
    (out,) = upgrade_listed_stations([node], [MatchResult("pse_closed", "X", 9, 100.0, "fuzzy", "")])
    assert out.station_class is UNK


# --- facts and scoring --------------------------------------------------------------------------------------------

SITE = Point(0, 0).buffer(10)


def _facts(*subs: Substation) -> dict[str, Fact]:  # type: ignore[type-arg]
    grid = build_grid_index(
        subs, [Line(1, 110_000, LineString([(0, 50_000), (1, 50_000)]))]
    )  # a far line only
    return {k: v for k, v in grid_facts(SITE, grid, CFG).items() if v is not None}


def _features(*subs: Substation) -> SiteFeatures:
    return SiteFeatures(**_facts(*subs))  # type: ignore[arg-type]


def _points(*subs: Substation) -> float:
    s = score_site(_features(*subs), CFG)
    return next(i for i in s.items if i.key == "grid_proximity").points


def test_the_rzepin_case_a_gpo_station_is_not_the_nearest_gpz() -> None:
    lubiechnia = _gpz(1, 700, GEN, "GPO Lubiechnia Wielka 110/20 kV")  # 0.7 km
    rzepin = _gpz(2, 4_000, OP, "GPZ Rzepin 110/15 kV")  # 4 km
    facts = _facts(lubiechnia, rzepin)
    assert facts["dist_gpz110_km"].value == pytest.approx(3.99, abs=0.02)  # type: ignore[union-attr]
    assert facts["dist_gpz110_operator_km"].value == pytest.approx(3.99, abs=0.02)  # type: ignore[union-attr]
    assert facts["dist_other_station_km"].value == pytest.approx(0.69, abs=0.02)  # type: ignore[union-attr]
    assert facts["other_station_class"].value == "generator"  # type: ignore[union-attr]
    assert facts["other_station_name"].value.startswith("GPO Lubiechnia")  # type: ignore[union-attr]


def test_gpz_points_full_for_operator_half_for_unknown_zero_for_generator_and_customer() -> None:
    full = _points(_gpz(1, 1_000, OP))
    assert full > 0
    assert _points(_gpz(1, 1_000, UNK)) == pytest.approx(full * CFG.grid.unknown_station_weight)
    assert _points(_gpz(1, 1_000, GEN)) == 0
    assert _points(_gpz(1, 1_000, CUST)) == 0


def test_a_near_generator_station_does_not_hide_a_farther_operator_gpz() -> None:
    assert _points(_gpz(1, 500, GEN), _gpz(2, 1_500, OP)) == pytest.approx(_points(_gpz(2, 1_500, OP)))


def test_a_near_unknown_station_beats_a_far_operator_gpz_only_when_it_scores_more() -> None:
    near_unknown, far_operator = _gpz(1, 500, UNK), _gpz(2, 6_500, OP)
    assert _points(near_unknown, far_operator) == pytest.approx(_points(near_unknown))
    assert _points(_gpz(1, 500, UNK), _gpz(2, 2_500, OP)) == pytest.approx(_points(_gpz(2, 2_500, OP)))


def test_context_line_for_a_generator_station_carries_no_points() -> None:
    s = score_site(_features(_gpz(1, 700, GEN, "GPO Lubiechnia"), _gpz(2, 1_000, OP)), CFG)
    ctx = next(i for i in s.items if i.key == "grid_other_station")
    assert (ctx.points, ctx.max_points) == (0.0, 0.0)
    assert "cable pooling" in ctx.explanation_pl and "0,7 km" in ctx.explanation_pl
    assert "GPO Lubiechnia" in ctx.explanation_pl


def test_no_context_line_when_the_other_station_is_far_or_absent() -> None:
    far = score_site(_features(_gpz(1, 20_000, GEN), _gpz(2, 1_000, OP)), CFG)
    assert all(i.key != "grid_other_station" for i in far.items)
    none = score_site(_features(_gpz(2, 1_000, OP)), CFG)
    assert all(i.key != "grid_other_station" for i in none.items)


def test_only_generator_stations_around_means_no_gpz_points_and_the_prefilter_ignores_them() -> None:
    grid = build_grid_index([_gpz(1, 1_000, GEN)], [])
    assert len(grid.gpz110) == 0 and len(grid.other_stations) == 1


def test_weight_one_switches_the_class_off_for_unknown() -> None:
    cfg = CFG.model_copy(update={"grid": CFG.grid.model_copy(update={"unknown_station_weight": 1.0})})
    f = _features(_gpz(1, 1_000, UNK))
    full = next(i for i in score_site(_features(_gpz(1, 1_000, OP)), cfg).items if i.key == "grid_proximity")
    unk = next(i for i in score_site(f, cfg).items if i.key == "grid_proximity")
    assert unk.points == pytest.approx(full.points)


def test_coverage_station_class_shares_per_voivodeship() -> None:
    rows = [
        {"teryt": "08", "name": "lubuskie", "station_class": "operator_gpz", "n": 6},
        {"teryt": "08", "name": "lubuskie", "station_class": "unknown", "n": 3},
        {"teryt": "08", "name": "lubuskie", "station_class": "generator", "n": 1},
        {"teryt": "02", "name": "dolnośląskie", "station_class": "customer", "n": 2},
    ]
    out = build_station_classes(rows)
    assert [a.teryt for a in out] == ["02", "08"]
    assert out[1].unknown_share == pytest.approx(0.3)
    assert (out[0].customer, out[0].unknown_share) == (2, 0.0)
    assert build_station_classes([]) == []


def _row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "features": {"dist_gpz110_km": {"value": 2.0, "confidence": "estimated", "source_id": "osm_power", "as_of": None}},
        "gpz_name": None, "gpz_dist_m": None, "gpz_class_source": None,
        "gpz_unk_name": None, "gpz_unk_dist_m": None, "gpz_unk_class_source": None,
        "other_name": None, "other_class": None, "other_dist_m": None,
    }  # fmt: skip
    return base | over


def test_mcp_nearest_gpz_prefers_operator_then_labelled_unknown_and_never_a_generator() -> None:
    from dclh_api.mcp.search import _nearest_gpz, _nearest_other_station

    both = _row(
        gpz_name="GPZ Rzepin", gpz_dist_m=4000, gpz_class_source="operator_list",
        gpz_unk_name="Bez nazwy", gpz_unk_dist_m=900,
        other_name="GPO Lubiechnia Wielka", other_class="generator", other_dist_m=700,
    )  # fmt: skip
    gpz = _nearest_gpz(both)
    assert (gpz["name"], gpz["distance_km"], gpz["station_class"], gpz["class_source"]) == (
        "GPZ Rzepin", 4.0, "operator_gpz", "operator_list",
    )  # fmt: skip
    assert gpz["class_note"] is None
    other = _nearest_other_station(both)
    assert other is not None and (other["name"], other["distance_km"], other["station_class"]) == (
        "GPO Lubiechnia Wielka", 0.7, "generator",
    )  # fmt: skip

    fallback = _nearest_gpz(_row(gpz_unk_name="Stacja X", gpz_unk_dist_m=1500))
    assert (fallback["name"], fallback["distance_km"], fallback["station_class"]) == (
        "Stacja X",
        1.5,
        "unknown",
    )
    assert "nieustalonym" in str(fallback["class_note"])

    nothing = _nearest_gpz(_row(other_name="GPO", other_class="generator", other_dist_m=700))
    assert nothing["name"] is None and nothing["station_class"] is None
    assert _nearest_other_station(_row()) is None
