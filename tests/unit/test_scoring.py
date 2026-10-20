"""Scoring engine tests (brief sections 6 and 10)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dclh_pipeline.config import ScoringConfig, load_scoring
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring import Fact, GridBadge, SiteFeatures, ZoningKind, ramp_down, score_site
from dclh_pipeline.scoring.types import SiteScore

CFG = load_scoring()


def F[T](value: T, source: str = "test", conf: Confidence = Confidence.ESTIMATED) -> Fact[T]:
    return Fact(value, source, None, conf)


def full_features(**overrides: object) -> SiteFeatures:
    """A site with every input known and an ideal profile; tests override single fields."""
    base = SiteFeatures(
        area_ha=F(20.0), compactness=F(0.8), parcel_count=F(3), expansion_ha=F(10.0),
        dist_line110_km=F(0.5), dist_gpz110_km=F(1.0), dist_node_220_400_km=F(10.0),
        independent_110_lines_near=F(2), dist_pse_planned_station_km=F(10.0), closed_switchyard_nearby=F(False),
        published_gpz_headroom_mw=F(5.0, conf=Confidence.VERIFIED), dist_pse_odb_km=F(5.0), powered_brownfield=F(False),
        zoning=F(ZoningKind.MPZP_PRODUCTION), dist_telecom_node_km=F(1.0), dist_big_city_km=F(25.0),
        dist_heat_network_km=F(2.0), psi_aid_pts=F(3.0), dist_res_cluster_km=F(5.0), env_decision_found=F(True),
        public_seller=F(True), protected_nature_pct=F(0.0), flood_10_or_1_pct=F(0.0), flood_0_2_pct=F(0.0),
        mining_area_pct=F(0.0), mining_terrain_pct=F(0.0), forest_pct=F(0.0), soil_class_i_ii_pct=F(0.0),
        soil_class_iii_pct=F(0.0), on_heritage_register=F(False), residential_min_m=F(800.0), residential_clear_ha=F(20.0),
        landscape_park_or_ochk=F(False), gas_pipeline_zone=F(False), airport_ols=F(False),
    )  # fmt: skip
    return replace(base, **overrides)  # type: ignore[arg-type]


def item(s: SiteScore, key: str):  # type: ignore[no-untyped-def]
    return next(i for i in s.items if i.key == key)


# --------------------------------------------------------------------------- shape and bounds


def test_ideal_site_scores_near_the_top() -> None:
    s = score_site(full_features(), CFG)
    assert not s.excluded and s.exclusions == []
    assert s.score >= 90
    assert s.completeness == 1.0
    assert s.grid_badge is GridBadge.PUBLISHED
    assert s.estimated_mw == pytest.approx(120.0)  # 20 ha x 6 MW/ha


def test_category_maxima_follow_the_weights() -> None:
    s = score_site(full_features(), CFG)
    maxima = {c.category: c.max_points for c in s.categories}
    assert maxima == {"grid": 45, "zoning": 25, "land": 15, "connectivity": 5, "bonus": 10}
    assert sum(maxima.values()) == 100


def test_items_explain_themselves() -> None:
    s = score_site(full_features(), CFG)
    for it in s.items:
        assert it.explanation_pl.strip() and it.label_pl.strip()
        if it.confidence is not Confidence.MISSING:
            assert it.source_id == "test"


def test_item_points_add_up_to_category_points_when_not_capped() -> None:
    s = score_site(
        full_features(dist_line110_km=F(2.5), dist_gpz110_km=F(7.0), powered_brownfield=F(False)), CFG
    )
    grid_items = [i for i in s.items if i.category == "grid"]
    grid = next(c for c in s.categories if c.category == "grid")
    assert sum(i.points for i in grid_items) == pytest.approx(grid.points)


def test_overflowing_category_shrinks_only_positive_items() -> None:
    s = score_site(
        full_features(closed_switchyard_nearby=F(True), powered_brownfield=F(True), dist_line110_km=F(0.0)),
        CFG,
    )
    grid_items = [i for i in s.items if i.category == "grid"]
    grid = next(c for c in s.categories if c.category == "grid")
    positives = sum(i.points for i in grid_items if i.points > 0)
    assert positives + (-CFG.grid.closed_switchyard_penalty * 45 / 100) == pytest.approx(grid.points)
    assert sum(i.points for i in grid_items) == pytest.approx(grid.points)
    assert item(s, "grid_closed").points == pytest.approx(-CFG.grid.closed_switchyard_penalty * 45 / 100)


# --------------------------------------------------------------------------- grid proximity (the 45-point category)


@pytest.mark.parametrize(
    ("line_km", "expected_fraction"),
    [(0.0, 1.0), (1.0, 1.0), (2.0, 0.5), (3.0, 0.0), (10.0, 0.0)],
)
def test_line_distance_ramp_matches_config(line_km: float, expected_fraction: float) -> None:
    assert ramp_down(line_km, CFG.grid.line110_km_full, CFG.grid.line110_km_zero) == pytest.approx(
        expected_fraction
    )


def test_proximity_uses_the_better_of_line_and_gpz() -> None:
    far_line_close_gpz = score_site(full_features(dist_line110_km=F(9.0), dist_gpz110_km=F(1.0)), CFG)
    close_line_far_gpz = score_site(full_features(dist_line110_km=F(0.5), dist_gpz110_km=F(30.0)), CFG)
    assert item(far_line_close_gpz, "grid_proximity").points == pytest.approx(
        item(close_line_far_gpz, "grid_proximity").points
    )


def test_large_campus_needs_a_node() -> None:
    big_with_node = score_site(full_features(area_ha=F(40.0)), CFG)  # 240 MW
    big_without = score_site(full_features(area_ha=F(40.0), dist_node_220_400_km=F(60.0)), CFG)
    assert big_with_node.large_campus and any("Duży kampus" in x for x in big_with_node.flags_pl)
    assert (
        item(big_with_node, "grid_node").max_points
        > item(score_site(full_features(), CFG), "grid_node").max_points
    )
    assert big_with_node.score - big_without.score > 5
    assert "poważna słabość" in item(big_without, "grid_node").explanation_pl


def test_closed_switchyard_is_a_penalty() -> None:
    clean = score_site(full_features(), CFG)
    closed = score_site(full_features(closed_switchyard_nearby=F(True)), CFG)
    assert closed.score < clean.score
    assert item(closed, "grid_closed").points == pytest.approx(-CFG.grid.closed_switchyard_penalty * 45 / 100)


def test_powered_brownfield_adds_points() -> None:
    base = full_features(dist_line110_km=F(2.5), dist_gpz110_km=F(7.0))
    assert score_site(replace(base, powered_brownfield=F(True)), CFG).score > score_site(base, CFG).score


def test_grid_badge() -> None:
    assert score_site(full_features(), CFG).grid_badge is GridBadge.PUBLISHED
    assert score_site(full_features(published_gpz_headroom_mw=None), CFG).grid_badge is GridBadge.PROXY
    none = score_site(
        full_features(
            published_gpz_headroom_mw=None,
            dist_line110_km=None,
            dist_gpz110_km=None,
            dist_node_220_400_km=None,
        ),
        CFG,
    )
    assert none.grid_badge is GridBadge.NONE


# --------------------------------------------------------------------------- zoning


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ZoningKind.MPZP_PRODUCTION, 25.0),
        (ZoningKind.PLAN_OGOLNY_SG, 20.0),
        (ZoningKind.PROCEDURE_IN_PROGRESS, 12.5),
        (ZoningKind.UNKNOWN, 7.5),
        (ZoningKind.OTHER, 0.0),
    ],
)
def test_zoning_points_scale_by_weight(kind: ZoningKind, expected: float) -> None:
    assert item(score_site(full_features(zoning=F(kind)), CFG), "zoning").points == pytest.approx(expected)


def test_no_plan_is_unknown_not_bad() -> None:
    unknown = score_site(full_features(zoning=None), CFG)
    other = score_site(full_features(zoning=F(ZoningKind.OTHER)), CFG)
    z = item(unknown, "zoning")
    assert z.confidence is Confidence.MISSING
    assert z.points == pytest.approx(7.5)  # config: unknown = 30
    assert unknown.score > other.score  # a missing plan must not rank below a known non-industrial plan
    assert z.upside_points == pytest.approx(25.0)
    assert "„nieznane”, nie „złe”" in z.explanation_pl
    assert "30% maksymalnej punktacji" in z.explanation_pl


# --------------------------------------------------------------------------- land


def test_size_ideal_band_beats_oversized() -> None:
    ideal = item(score_site(full_features(area_ha=F(20.0)), CFG), "land_size").points
    big = item(score_site(full_features(area_ha=F(60.0)), CFG), "land_size").points
    huge = score_site(full_features(area_ha=F(120.0)), CFG)
    assert ideal > big > item(huge, "land_size").points
    assert huge.split_suggested and any("do podziału" in x for x in huge.flags_pl)


def test_fewer_parcels_score_higher() -> None:
    few = item(score_site(full_features(parcel_count=F(3)), CFG), "land_parcels").points
    many = item(score_site(full_features(parcel_count=F(15)), CFG), "land_parcels").points
    lots = item(score_site(full_features(parcel_count=F(40)), CFG), "land_parcels").points
    assert few > many > lots == 0.0


def test_compactness_below_minimum_scores_zero() -> None:
    assert item(score_site(full_features(compactness=F(0.2)), CFG), "land_compactness").points == 0.0
    assert item(score_site(full_features(compactness=F(1.0)), CFG), "land_compactness").points > 0


def test_estimated_power_below_minimum_is_flagged() -> None:
    s = score_site(full_features(area_ha=F(8.0)), CFG)  # 48 MW
    assert any("poniżej minimum" in x for x in s.flags_pl)


# --------------------------------------------------------------------------- bonus and penalties


def test_bonus_is_capped_at_its_weight() -> None:
    s = score_site(full_features(), CFG)  # heat 3 + psi 3 + res 2 + env 3 + seller 2 = 13 > 10
    bonus = next(c for c in s.categories if c.category == "bonus")
    assert bonus.points == pytest.approx(10.0)
    assert sum(i.points for i in s.items if i.category == "bonus") == pytest.approx(10.0)


def test_penalties_reduce_the_score() -> None:
    clean = score_site(full_features(), CFG)
    s = score_site(
        full_features(residential_min_m=F(200.0), gas_pipeline_zone=F(True), flood_0_2_pct=F(5.0)), CFG
    )
    assert clean.score - s.score == pytest.approx(10 + 5 + 10)
    assert item(s, "pen_residential").explanation_pl.startswith("Zabudowa mieszkaniowa w odległości 200 m")


def test_housing_closer_than_100_m_is_penalised_when_the_site_is_not_excluded() -> None:
    # A house on the edge of a site with 20 ha clear of housing: not excluded, but residents are a risk.
    s = score_site(full_features(residential_min_m=F(3.0)), CFG)
    assert not s.excluded
    assert item(s, "pen_residential").points == -10
    assert item(s, "pen_residential").explanation_pl.startswith("Zabudowa mieszkaniowa w odległości 3 m")


def test_soil_class_iii_threshold() -> None:
    assert item(score_site(full_features(soil_class_iii_pct=F(30.0)), CFG), "pen_soil_iii").points == 0
    assert item(score_site(full_features(soil_class_iii_pct=F(31.0)), CFG), "pen_soil_iii").points == -8


# --------------------------------------------------------------------------- hard filters


@pytest.mark.parametrize(
    ("override", "key", "reason_fragment"),
    [
        (dict(area_ha=F(9.5)), "area_below_min", "za mała powierzchnia"),
        (dict(protected_nature_pct=F(21.0)), "protected_nature_pct_over", "Natura 2000"),
        (dict(flood_10_or_1_pct=F(1.0)), "flood_10_or_1_pct", "strefa zalewowa"),
        (dict(mining_area_pct=F(5.0)), "mining_area_active", "obszar górniczy"),
        (dict(forest_pct=F(31.0)), "forest_pct_over", "las"),
        (dict(soil_class_i_ii_pct=F(31.0)), "soil_class_I_II_pct_over", "gleby klas I–II"),
        (dict(on_heritage_register=F(True)), "heritage_register", "rejestrze zabytków"),
        (
            dict(residential_min_m=F(5.0), residential_clear_ha=F(9.5)),
            "residential_within_m",
            "zabudowa mieszkaniowa",
        ),
    ],
)
def test_each_hard_filter_excludes_with_a_reason(
    override: dict[str, object], key: str, reason_fragment: str
) -> None:
    s = score_site(full_features(**override), CFG)
    assert s.excluded
    assert [e.key for e in s.exclusions] == [key]
    assert reason_fragment in s.exclusions[0].reason_pl
    assert s.score > 0  # an excluded site is still scored, so the list can show why it was excluded


@pytest.mark.parametrize(
    "override",
    [
        dict(area_ha=F(10.0)),  # exactly the minimum is allowed
        dict(protected_nature_pct=F(20.0)),
        dict(forest_pct=F(30.0)),
        dict(soil_class_i_ii_pct=F(30.0)),
        dict(residential_min_m=F(100.0)),
        dict(residential_min_m=F(0.0), residential_clear_ha=F(10.0)),  # a house on the edge, 10 ha clear
    ],
)
def test_thresholds_are_inclusive_on_the_safe_side(override: dict[str, object]) -> None:
    assert not score_site(full_features(**override), CFG).excluded


def test_small_site_is_excluded_for_size() -> None:
    """The brief's golden set: Data4 Jawczyce, Atman Duchnice and Switch Mory are under 10 ha."""
    s = score_site(full_features(area_ha=F(4.5)), CFG)
    assert s.excluded and "za mała powierzchnia" in s.exclusions[0].reason_pl


def test_penalty_mode_subtracts_instead_of_excluding() -> None:
    raw = CFG.model_dump()
    raw["hard_filters"]["forest_pct_over"]["mode"] = "penalty"
    raw["hard_filters"]["forest_pct_over"]["penalty_points"] = 12
    cfg = ScoringConfig.model_validate(raw)
    clean = score_site(full_features(), cfg)
    forest = score_site(full_features(forest_pct=F(50.0)), cfg)
    assert not forest.excluded
    assert clean.score - forest.score == pytest.approx(12)


def test_missing_constraint_data_never_excludes() -> None:
    s = score_site(full_features(forest_pct=None, flood_10_or_1_pct=None, on_heritage_register=None), CFG)
    assert not s.excluded
    assert s.completeness < 1.0


# --------------------------------------------------------------------------- missing data, completeness, upside


def test_missing_data_scores_zero_and_shows_as_unknown() -> None:
    s = score_site(full_features(dist_telecom_node_km=None), CFG)
    it = item(s, "conn_telecom")
    assert it.points == 0 and it.confidence is Confidence.MISSING
    assert it.upside_points == pytest.approx(it.max_points)
    assert it.explanation_pl.startswith("Brak danych")


def test_completeness_drops_as_data_goes_missing_and_upside_recovers() -> None:
    complete = score_site(full_features(), CFG)
    gaps = score_site(full_features(dist_telecom_node_km=None, dist_big_city_km=None, parcel_count=None), CFG)
    assert complete.completeness == 1.0
    assert gaps.completeness < complete.completeness
    assert gaps.score < complete.score
    assert (
        gaps.upside_score >= complete.score - 1e-9
    )  # best case for the gaps is at least as good as the full data


def test_an_empty_site_is_excluded_and_claims_no_upside() -> None:
    """No area means the 10 ha minimum cannot be shown to hold: excluded, and upside is not 100 (it was)."""
    s = score_site(SiteFeatures(), CFG)
    assert s.completeness == 0.0
    assert s.excluded and s.estimated_mw is None
    assert [e.key for e in s.exclusions] == ["area_below_min"]
    assert "nieznana powierzchnia" in s.exclusions[0].reason_pl
    assert s.grid_badge is GridBadge.NONE
    assert s.score < 10 and s.upside_score == pytest.approx(s.score)


@pytest.mark.parametrize("area", [None, F(math.nan), F(math.inf)], ids=["none", "nan", "inf"])
def test_a_site_without_a_usable_area_is_excluded_and_not_shown_as_upside_100(
    area: Fact[float] | None,
) -> None:
    """Everything else is ideal, but with no area the upside used to reach 100. Now it equals the score."""
    s = score_site(full_features(area_ha=area), CFG)
    assert s.excluded and [e.key for e in s.exclusions] == ["area_below_min"]
    assert s.upside_score == pytest.approx(s.score)
    assert s.upside_score < 100
    assert s.estimated_mw is None and s.large_campus is False and s.split_suggested is False
    land = item(s, "land_size")
    assert land.confidence is Confidence.MISSING and land.points == 0
    assert not any(
        "ha to duży teren" in i.explanation_pl or "ha mieści się" in i.explanation_pl for i in s.items
    )


@pytest.mark.parametrize(
    "field", ["forest_pct", "flood_10_or_1_pct", "dist_line110_km", "residential_min_m", "compactness"]
)
def test_nan_in_a_feature_scores_exactly_like_missing(field: str) -> None:
    """NaN fails every comparison, so it must be treated as missing rather than slipping past thresholds."""
    nan = score_site(full_features(**{field: F(math.nan)}), CFG)
    missing = score_site(full_features(**{field: None}), CFG)
    assert nan.score == pytest.approx(missing.score)
    assert nan.upside_score == pytest.approx(missing.upside_score)
    assert nan.excluded == missing.excluded and nan.completeness == pytest.approx(missing.completeness)


@pytest.mark.parametrize(("area", "excluded"), [(9.999, True), (10.0, False)])
def test_minimum_area_boundary_and_the_mw_flag_never_contradict(area: float, excluded: bool) -> None:
    """9.999 ha is 59.994 MW. The flag must not print "60 MW is below the 60 MW minimum"."""
    s = score_site(full_features(area_ha=F(area)), CFG)
    assert s.excluded is excluded
    assert not any("poniżej minimum" in flag for flag in s.flags_pl)
    if excluded:
        assert "9,999 ha < 10 ha" in s.exclusions[0].reason_pl


def test_minimum_mw_flag_prints_the_figure_it_compares() -> None:
    s = score_site(full_features(area_ha=F(9.9)), CFG)  # 59.4 MW
    assert "Szacowana moc 59,4 MW jest poniżej minimum 60 MW." in s.flags_pl


def test_no_missing_data_means_upside_equals_score() -> None:
    s = score_site(full_features(), CFG)
    assert s.upside_score == pytest.approx(s.score)


# --------------------------------------------------------------------------- properties (hypothesis)

km = st.floats(min_value=0.0, max_value=200.0, allow_nan=False)
pct = st.floats(min_value=0.0, max_value=100.0, allow_nan=False)


@settings(max_examples=150, deadline=None)
@given(near=km, extra=st.floats(min_value=0.0, max_value=100.0))
def test_a_closer_line_never_lowers_the_score(near: float, extra: float) -> None:
    closer = score_site(full_features(dist_line110_km=F(near), dist_gpz110_km=F(40.0)), CFG)
    farther = score_site(full_features(dist_line110_km=F(near + extra), dist_gpz110_km=F(40.0)), CFG)
    assert closer.score >= farther.score - 1e-9


@settings(max_examples=150, deadline=None)
@given(near=km, extra=st.floats(min_value=0.0, max_value=100.0))
def test_a_closer_gpz_never_lowers_the_score(near: float, extra: float) -> None:
    closer = score_site(full_features(dist_line110_km=F(40.0), dist_gpz110_km=F(near)), CFG)
    farther = score_site(full_features(dist_line110_km=F(40.0), dist_gpz110_km=F(near + extra)), CFG)
    assert closer.score >= farther.score - 1e-9


@settings(max_examples=100, deadline=None)
@given(near=km, extra=st.floats(min_value=0.0, max_value=100.0))
def test_closer_telecom_node_and_city_never_lower_the_score(near: float, extra: float) -> None:
    a = score_site(full_features(dist_telecom_node_km=F(near), dist_big_city_km=F(near)), CFG)
    b = score_site(full_features(dist_telecom_node_km=F(near + extra), dist_big_city_km=F(near + extra)), CFG)
    assert a.score >= b.score - 1e-9


@settings(max_examples=100, deadline=None)
@given(few=st.integers(1, 60), more=st.integers(0, 60))
def test_fewer_parcels_never_lower_the_score(few: int, more: int) -> None:
    a = score_site(full_features(parcel_count=F(few)), CFG)
    b = score_site(full_features(parcel_count=F(few + more)), CFG)
    assert a.score >= b.score - 1e-9


@settings(max_examples=100, deadline=None)
@given(c=st.floats(0.0, 1.0), d=st.floats(0.0, 1.0))
def test_better_compactness_never_lowers_the_score(c: float, d: float) -> None:
    lo, hi = sorted((c, d))
    assert (
        score_site(full_features(compactness=F(hi)), CFG).score
        >= score_site(full_features(compactness=F(lo)), CFG).score - 1e-9
    )


@settings(max_examples=100, deadline=None)
@given(a=pct, b=pct)
def test_more_flood_overlap_never_raises_the_score(a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    assert (
        score_site(full_features(flood_0_2_pct=F(lo)), CFG).score
        >= score_site(full_features(flood_0_2_pct=F(hi)), CFG).score - 1e-9
    )


_opt_km = st.one_of(st.none(), km.map(F))
_opt_pct = st.one_of(st.none(), pct.map(F))
_opt_bool = st.one_of(st.none(), st.booleans().map(F))


@st.composite
def random_features(draw: st.DrawFn) -> SiteFeatures:
    return SiteFeatures(
        area_ha=draw(st.one_of(st.none(), st.floats(0.1, 500.0).map(F))),
        compactness=draw(st.one_of(st.none(), st.floats(0.0, 1.0).map(F))),
        parcel_count=draw(st.one_of(st.none(), st.integers(1, 200).map(F))),
        expansion_ha=draw(st.one_of(st.none(), st.floats(0.0, 300.0).map(F))),
        dist_line110_km=draw(_opt_km), dist_gpz110_km=draw(_opt_km), dist_node_220_400_km=draw(_opt_km),
        independent_110_lines_near=draw(st.one_of(st.none(), st.integers(0, 6).map(F))),
        dist_pse_planned_station_km=draw(_opt_km), closed_switchyard_nearby=draw(_opt_bool),
        published_gpz_headroom_mw=draw(st.one_of(st.none(), st.floats(0.0, 50.0).map(F))),
        dist_pse_odb_km=draw(_opt_km), powered_brownfield=draw(_opt_bool),
        zoning=draw(st.one_of(st.none(), st.sampled_from(list(ZoningKind)).map(F))),
        dist_telecom_node_km=draw(_opt_km), dist_big_city_km=draw(_opt_km), dist_heat_network_km=draw(_opt_km),
        psi_aid_pts=draw(st.one_of(st.none(), st.floats(0.0, 5.0).map(F))), dist_res_cluster_km=draw(_opt_km),
        env_decision_found=draw(_opt_bool), public_seller=draw(_opt_bool),
        protected_nature_pct=draw(_opt_pct), flood_10_or_1_pct=draw(_opt_pct), flood_0_2_pct=draw(_opt_pct),
        mining_area_pct=draw(_opt_pct), mining_terrain_pct=draw(_opt_pct), forest_pct=draw(_opt_pct),
        soil_class_i_ii_pct=draw(_opt_pct), soil_class_iii_pct=draw(_opt_pct), on_heritage_register=draw(_opt_bool),
        residential_min_m=draw(st.one_of(st.none(), st.floats(0.0, 3000.0).map(F))),
        residential_clear_ha=draw(st.one_of(st.none(), st.floats(0.0, 500.0).map(F))),
        landscape_park_or_ochk=draw(_opt_bool), gas_pipeline_zone=draw(_opt_bool), airport_ols=draw(_opt_bool),
    )  # fmt: skip


@settings(max_examples=300, deadline=None)
@given(features=random_features())
def test_any_site_yields_a_well_formed_score(features: SiteFeatures) -> None:
    s = score_site(features, CFG)
    assert 0.0 <= s.score <= 100.0
    assert s.score <= s.upside_score <= 100.0
    assert 0.0 <= s.completeness <= 1.0
    assert all(e.reason_pl for e in s.exclusions)
    assert all(i.explanation_pl for i in s.items)
    for c in s.categories:
        assert -1e-9 <= c.points <= c.max_points + 1e-9
        assert c.points <= c.upside_points + 1e-9
