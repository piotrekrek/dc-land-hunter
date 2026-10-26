"""The 60 MW grid tier of small plots (PLAN.md, "Small plots with 60 MW grid")."""

from __future__ import annotations

import pytest

from dclh_pipeline.config import load_scoring
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring import SiteFeatures
from dclh_pipeline.scoring.grid60 import Grid60Tier, grid60_tier
from tests.unit.test_scoring import F

CFG = load_scoring()
G = CFG.small.grid60


def tier(**facts: object) -> Grid60Tier | None:
    f = grid60_tier(SiteFeatures(**facts), CFG)  # type: ignore[arg-type]
    return f.value if f else None


def test_defaults_match_the_plan() -> None:
    assert (CFG.small.ha_min, CFG.small.ha_max) == (0.5, 2.0)
    assert (G.strong_gpz110_km, G.strong_min_110_lines, G.strong_node_km) == (1.0, 2, 3.0)
    assert (G.possible_gpz110_km, G.possible_line110_km) == (2.0, 0.5)


def test_gpz_close_with_two_lines_is_strong() -> None:
    assert tier(dist_gpz110_km=F(1.0), independent_110_lines_near=F(2)) is Grid60Tier.STRONG


def test_gpz_close_with_one_line_is_only_possible() -> None:
    assert tier(dist_gpz110_km=F(0.3), independent_110_lines_near=F(1)) is Grid60Tier.POSSIBLE


def test_gpz_close_with_unknown_line_count_is_never_strong() -> None:
    assert tier(dist_gpz110_km=F(0.3)) is Grid60Tier.POSSIBLE


def test_node_close_is_strong_on_its_own() -> None:
    assert tier(dist_node_220_400_km=F(3.0)) is Grid60Tier.STRONG


def test_closed_switchyard_downgrades_strong() -> None:
    assert tier(dist_node_220_400_km=F(1.0), closed_switchyard_nearby=F(True)) is Grid60Tier.POSSIBLE
    both = {"dist_gpz110_km": F(0.5), "independent_110_lines_near": F(3), "closed_switchyard_nearby": F(True)}
    assert tier(**both) is Grid60Tier.POSSIBLE


@pytest.mark.parametrize(
    "facts",
    [
        {"dist_gpz110_km": F(2.0)},
        {"dist_line110_km": F(0.5)},
        {"dist_gpz110_km": F(1.5), "independent_110_lines_near": F(4)},
    ],
)
def test_possible(facts: dict[str, object]) -> None:
    assert tier(**facts) is Grid60Tier.POSSIBLE


@pytest.mark.parametrize(
    "facts",
    [
        {},  # no grid data at all: no tier, never a guess
        {"dist_gpz110_km": F(2.01), "dist_line110_km": F(0.51), "dist_node_220_400_km": F(3.01)},
        {"independent_110_lines_near": F(5)},  # lines in the N-1 radius but nothing close
    ],
)
def test_too_far_or_unknown_has_no_tier(facts: dict[str, object]) -> None:
    assert tier(**facts) is None


def test_tier_is_an_estimate_with_the_deciding_source() -> None:
    f = grid60_tier(SiteFeatures(dist_line110_km=F(0.2, source="osm_power")), CFG)
    assert f is not None
    assert (f.source_id, f.confidence) == ("osm_power", Confidence.ESTIMATED)


def test_small_size_range_must_be_ordered() -> None:
    from dclh_pipeline.config import SmallSites

    with pytest.raises(ValueError, match="ha_min"):
        SmallSites(ha_min=2.0, ha_max=2.0)
