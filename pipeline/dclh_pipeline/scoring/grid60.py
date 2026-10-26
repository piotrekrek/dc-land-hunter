"""The 60 MW grid tier of a small plot (PLAN.md, "Small plots with 60 MW grid").

On a plot of up to 2 ha the 60 MW comes from the grid nearby, not from the land (area × density gives about
12 MW). No public source gives 110 kV load headroom, so the tier is a distance proxy, always `estimated`.
A missing fact never counts as close: without grid data a plot has no tier.
"""

from __future__ import annotations

from enum import StrEnum

from dclh_pipeline.config import ScoringConfig
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring.types import Fact, SiteFeatures


class Grid60Tier(StrEnum):
    STRONG = "strong"  # "Sieć 60 MW+: prawdopodobnie"
    POSSIBLE = "possible"  # "Sieć 60 MW+: do sprawdzenia"


def _within(fact: Fact[float] | None, km: float) -> Fact[float] | None:
    return fact if fact is not None and fact.value <= km else None


def grid60_tier(f: SiteFeatures, cfg: ScoringConfig) -> Fact[Grid60Tier] | None:
    g = cfg.small.grid60
    lines = f.independent_110_lines_near
    gpz_strong = _within(f.dist_gpz110_km, g.strong_gpz110_km)
    if gpz_strong is not None and (lines is None or lines.value < g.strong_min_110_lines):
        gpz_strong = None
    strong = gpz_strong or _within(f.dist_node_220_400_km, g.strong_node_km)
    closed = f.closed_switchyard_nearby is not None and f.closed_switchyard_nearby.value
    if strong is not None and not closed:
        return Fact(Grid60Tier.STRONG, strong.source_id, strong.as_of, Confidence.ESTIMATED)
    possible = (
        strong
        or _within(f.dist_gpz110_km, g.possible_gpz110_km)
        or _within(f.dist_line110_km, g.possible_line110_km)
    )
    if possible is None:
        return None
    return Fact(Grid60Tier.POSSIBLE, possible.source_id, possible.as_of, Confidence.ESTIMATED)
