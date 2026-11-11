"""Profile verdicts (DC budowane / DC kontenerowe)."""

from __future__ import annotations

import pytest

from dclh_pipeline.config import ProfilesConfig, load_profiles
from dclh_pipeline.profiles import Category, profile_verdict, profile_verdicts
from dclh_pipeline.scoring import SiteFeatures, ZoningKind
from tests.unit.test_scoring import F, full_features

CFG = load_profiles()


def verdict(name: str, **overrides: object):  # type: ignore[no-untyped-def]
    return profile_verdict(full_features(**overrides), name, CFG)


def keys(items: list) -> set[str]:  # type: ignore[type-arg]
    return {i.key for i in items}


def test_clean_industrial_site_is_green_for_built() -> None:
    v = verdict("built")
    assert v.category is Category.GREEN
    assert v.blockers == []
    assert v.score >= CFG.thresholds.green
    assert v.positives  # says why


def test_manual_checks_are_always_listed_but_never_block() -> None:
    v = verdict("built")
    assert {"road_access", "closed_area", "mpzp_text", "title"} <= keys(
        v.to_check
    )  # no road measured: manual
    assert not any(c.blocking for c in v.to_check)


def test_only_the_container_profile_is_told_about_container_bans_in_the_plan() -> None:
    def mpzp_text(name: str) -> str:
        return next(c.text_pl for c in verdict(name).to_check if c.key == "mpzp_text")

    assert "kontenerowych" in mpzp_text("container")
    assert "kontenerowych" not in mpzp_text("built")


@pytest.mark.parametrize("name", ["built", "container"])
@pytest.mark.parametrize(
    ("field", "key"),
    [
        ("flood_10_or_1_pct", "flood"),
        ("protected_nature_pct", "nature"),
        ("forest_pct", "forest"),
        ("mining_area_pct", "mining"),
    ],
)
def test_overlap_with_protected_land_is_red_for_both(name: str, field: str, key: str) -> None:
    v = verdict(name, **{field: F(15.0)})
    assert v.category is Category.RED
    assert key in keys(v.blockers)


@pytest.mark.parametrize("name", ["built", "container"])
def test_heritage_register_is_red(name: str) -> None:
    assert verdict(name, on_heritage_register=F(True)).category is Category.RED


def test_area_decides_between_profiles() -> None:
    small = {"area_ha": F(1.0)}
    assert verdict("built", **small).category is Category.RED
    assert "area" in keys(verdict("built", **small).blockers)
    assert verdict("container", **small).category is Category.GREEN
    assert verdict("container", area_ha=F(0.1)).category is Category.RED


def test_unknown_area_needs_manual_check_not_a_pass() -> None:
    v = verdict("container", area_ha=None)
    assert v.category is Category.MANUAL
    assert "area" in keys(v.to_check)


@pytest.mark.parametrize("name", ["built", "container"])
def test_unknown_zoning_is_manual_not_red(name: str) -> None:
    for z in (None, F(ZoningKind.UNKNOWN), F(ZoningKind.PROCEDURE_IN_PROGRESS)):
        v = verdict(name, zoning=z)
        assert v.category is Category.MANUAL
        assert "zoning" in keys(v.to_check)


def test_other_zoning_blocks_built_but_container_must_check() -> None:
    other = {"zoning": F(ZoningKind.OTHER)}
    assert verdict("built", **other).category is Category.RED
    c = verdict("container", **other)
    assert c.category is Category.MANUAL
    assert any(t.key == "zoning" and t.blocking for t in c.to_check)


def test_plan_ogolny_never_green_without_ouz() -> None:
    v = verdict("built", zoning=F(ZoningKind.PLAN_OGOLNY_SG))
    assert v.category is Category.YELLOW
    assert "ouz" in keys(v.to_check)


def test_housing_buffer_differs_per_profile() -> None:
    near = {"residential_min_m": F(200.0)}
    built, container = verdict("built", **near), verdict("container", **near)
    assert container.score > built.score
    assert container.category is Category.GREEN
    assert built.category is not Category.GREEN
    # 200 m is below the built minimum of 250 m (the housing input scores zero); 300 m earns some points
    assert verdict("built", residential_min_m=F(300.0)).score > built.score
    assert verdict("built", residential_min_m=F(100.0)).score == built.score


def test_gpz_distance_is_stricter_for_container() -> None:
    far = {"dist_gpz110_km": F(4.0)}
    assert verdict("built", **far).score > verdict("container", **far).score


def test_missing_input_counts_neutral_and_is_listed() -> None:
    v = verdict("built", dist_gpz110_km=None)
    assert "gpz110" in keys(v.to_check)
    assert v.score < verdict("built").score


@pytest.mark.parametrize("name", ["built", "container"])
def test_missing_heritage_data_is_listed_but_does_not_cap_green(name: str) -> None:
    # No source feeds the register, so capping on it made green unreachable for every stored site.
    v = verdict(name, on_heritage_register=None)
    assert v.category is Category.GREEN
    assert "heritage" in keys(v.to_check)


def test_headroom_is_measured_against_the_60_mw_target() -> None:
    for name in ("built", "container"):
        low, half, full = (verdict(name, published_gpz_headroom_mw=F(mw)).score for mw in (10.0, 30.0, 60.0))
        assert low < half < full
        assert verdict(name, published_gpz_headroom_mw=F(90.0)).score == full


def test_container_needs_half_a_hectare() -> None:
    assert verdict("container", area_ha=F(0.4)).category is Category.RED
    assert verdict("container", area_ha=F(0.5)).category is Category.GREEN


def test_missing_flood_data_caps_green_at_yellow() -> None:
    v = verdict("built", flood_10_or_1_pct=None)
    assert v.category is Category.YELLOW
    assert "flood" in keys(v.to_check)


def test_everything_missing_is_never_green() -> None:
    v = profile_verdicts(SiteFeatures(), CFG)
    assert all(x.category is Category.MANUAL for x in v.values())


def test_low_score_without_blockers_is_red() -> None:
    v = verdict("built", residential_min_m=F(10.0), dist_gpz110_km=F(30.0), published_gpz_headroom_mw=F(0.0))
    assert v.blockers == []
    assert v.category is Category.RED


def test_config_rejects_bad_values() -> None:
    raw = CFG.model_dump()
    raw["thresholds"] = {"green": 40, "yellow": 60}
    with pytest.raises(ValueError, match="yellow"):
        ProfilesConfig.model_validate(raw)
    raw = CFG.model_dump()
    del raw["profiles"]["container"]
    with pytest.raises(ValueError, match="exactly"):
        ProfilesConfig.model_validate(raw)


def test_verdicts_serialise_into_the_api_model() -> None:
    from dclh_api.schemas import ProfileVerdictOut
    from dclh_pipeline.scoring.run import to_jsonable

    stored = to_jsonable(profile_verdicts(full_features(flood_10_or_1_pct=F(5.0)), CFG))
    assert set(stored) == {"built", "container"}
    for name, raw in stored.items():
        out = ProfileVerdictOut.model_validate(raw)
        assert out.profile == name
        assert out.category == "red"
        assert out.blockers[0].key == "flood"


def test_features_survive_the_stored_json_round_trip() -> None:
    from dclh_pipeline.profiles import features_from_detail
    from dclh_pipeline.scoring.run import to_jsonable

    original = full_features(
        flood_10_or_1_pct=F(5.0), zoning=F(ZoningKind.PLAN_OGOLNY_SG), mining_area_pct=None
    )
    rebuilt = features_from_detail(to_jsonable(original))
    assert rebuilt == original
    assert profile_verdicts(rebuilt, CFG) == profile_verdicts(original, CFG)


def road_text(v) -> str:  # type: ignore[no-untyped-def]
    return next(c.text_pl for c in v.to_check if c.key == "road_access")


@pytest.mark.parametrize("name", ["built", "container"])
def test_a_public_road_at_the_boundary_is_a_plus_and_the_legal_access_is_still_to_check(name: str) -> None:
    v = verdict(name, dist_public_road_m=F(8.0))
    assert any(p.key == "road" and "8 m" in p.text_pl for p in v.positives) or v.positives
    assert "księdze wieczystej" in road_text(v) and not any(
        c.blocking for c in v.to_check if c.key == "road_access"
    )


def test_a_road_some_distance_away_is_named_with_its_distance_and_an_easement() -> None:
    text = road_text(verdict("built", dist_public_road_m=F(120.0)))
    assert "120 m" in text and "służebności" in text


def test_a_road_far_away_caps_the_verdict_at_yellow_but_never_makes_it_red() -> None:
    near = verdict("built", dist_public_road_m=F(10.0))
    far = verdict("built", dist_public_road_m=F(900.0))
    assert near.category is Category.GREEN
    assert far.category is Category.YELLOW and "900 m" in road_text(far)
    assert far.blockers == []  # we cannot know about an easement, so distance alone never rejects


def test_an_unmeasured_road_keeps_the_old_manual_wording() -> None:
    assert road_text(verdict("built")) == "Dostęp do drogi publicznej (EGiB, księga wieczysta)"


@pytest.mark.parametrize("name", ["built", "container"])
def test_a_forest_sliver_under_0_10_ha_does_not_exclude(name: str) -> None:
    """Parcels (EGiB) and forest outlines (BDOT10k) are drawn apart; their seams leave slivers of square metres.

    A forest is at least 0.10 ha (ustawa o lasach, art. 3), so a smaller overlap is no reason to drop a site.
    """
    v = verdict(name, area_ha=F(30.0), forest_pct=F(0.15))  # 0.045 ha
    assert "forest" not in keys(v.blockers)


def test_forest_of_0_10_ha_or_more_excludes_and_says_how_much() -> None:
    v = verdict("built", area_ha=F(30.0), forest_pct=F(0.5))  # 0.15 ha
    assert v.category is Category.RED
    (forest,) = [b for b in v.blockers if b.key == "forest"]
    assert forest.text_pl == "las: 0,15 ha (<1% terenu)"


def test_forest_on_a_site_of_unknown_area_still_excludes() -> None:
    v = verdict("container", area_ha=None, forest_pct=F(0.15))
    assert "forest" in keys(v.blockers)


def test_a_small_overlap_is_shown_as_under_1_percent_not_0() -> None:
    v = verdict("built", flood_10_or_1_pct=F(0.2))
    (flood,) = [b for b in v.blockers if b.key == "flood"]
    assert flood.text_pl == "strefa zalewowa 10% lub 1%: <1% terenu"
