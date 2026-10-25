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
    assert {"road_access", "closed_area", "mpzp_text", "title"} <= keys(v.to_check)
    assert not any(c.blocking for c in v.to_check)


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
