"""One scoring run: candidate sites and loaded layers → scored sites → the `sites` table (brief 5, step 5).

A run replaces the sites of its own powiat only (`write_sites`), so a site that disappeared from that powiat's
input disappears from the list too, while the other powiats keep their rows.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from shapely import wkb
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.config import ProfilesConfig, ScoringConfig, Target, load_profiles
from dclh_pipeline.model.features import SiteLayers, build_site_features
from dclh_pipeline.model.sites import Site
from dclh_pipeline.profiles import profile_verdicts
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.grid60 import Grid60Tier, grid60_tier
from dclh_pipeline.scoring.types import Fact, SiteFeatures, SiteScore

SiteKind = Literal["standard", "small"]


@dataclass(frozen=True)
class ScoredSite:
    site: Site
    features: SiteFeatures
    score: SiteScore
    kind: SiteKind = "standard"  # "small": a plot of up to 2 ha where the grid carries the 60 MW
    grid60: Fact[Grid60Tier] | None = None  # set on small sites only


def small_site_config(cfg: ScoringConfig) -> ScoringConfig:
    """The scoring config for small plots: their size range replaces the 10 ha minimum and ideal range.

    Their power comes from the grid, not from area × density, so the "below 60 MW" flag does not apply.
    """
    s = cfg.small
    target = Target(
        **{**cfg.target.model_dump(), "ha_min": s.ha_min, "ha_ideal": (s.ha_min, s.ha_max), "mw_min": 0}
    )
    return cfg.model_copy(update={"target": target})


def score_candidates(
    sites: Iterable[Site],
    layers: SiteLayers,
    cfg: ScoringConfig,
    *,
    parcels_as_of: datetime | None = None,
    kind: SiteKind = "standard",
) -> list[ScoredSite]:
    """Compute features and score every site. Excluded sites are kept, to show them greyed out.

    Small sites are scored with `small_site_config` and get their grid tier; one without a tier is dropped.
    """
    out: list[ScoredSite] = []
    scoring = small_site_config(cfg) if kind == "small" else cfg
    for site in sites:
        features = build_site_features(site, layers, scoring, parcels_as_of=parcels_as_of)
        tier = grid60_tier(features, cfg) if kind == "small" else None
        if kind == "small" and tier is None:
            continue
        out.append(ScoredSite(site, features, score_site(features, scoring), kind, tier))
    return sorted(out, key=lambda s: s.site.site_id)


def to_jsonable(value: Any) -> Any:
    """Dataclasses, enums and datetimes as plain JSON values (enums by value, datetimes as ISO 8601)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value


_INSERT = text(
    """
    INSERT INTO sites (
        site_id, powiat, zone_id, parcel_ids, area_ha, compactness, estimated_mw, score, upside_score,
        completeness, excluded, grid_badge, large_campus, split_suggested, flags, detail, scored_at, geom,
        profiles, verdict_built, verdict_container, kind, grid60
    ) VALUES (
        :site_id, :powiat, :zone_id, :parcel_ids, :area_ha, :compactness, :estimated_mw, :score,
        :upside_score, :completeness, :excluded, :grid_badge, :large_campus, :split_suggested, :flags,
        CAST(:detail AS jsonb),
        :scored_at, ST_SetSRID(ST_GeomFromWKB(:wkb), 2180),
        CAST(:profiles AS jsonb), :verdict_built, :verdict_container, :kind, :grid60
    )
    """
)


def _row(item: ScoredSite, scored_at: datetime, powiat: str, profiles: ProfilesConfig) -> dict[str, Any]:
    s, sc = item.site, item.score
    verdicts = profile_verdicts(item.features, profiles)
    detail = {"score": to_jsonable(sc), "features": to_jsonable(item.features)}
    if item.grid60 is not None:
        detail["grid60"] = to_jsonable(item.grid60)
    return {
        "site_id": s.site_id,
        "powiat": powiat,
        "zone_id": s.zone_id,
        "parcel_ids": list(s.parcel_ids),
        "area_ha": s.area_ha,
        "compactness": s.compactness,
        "estimated_mw": sc.estimated_mw if sc.estimated_mw is not None else s.estimated_mw,
        "score": sc.score,
        "upside_score": sc.upside_score,
        "completeness": sc.completeness,
        "excluded": sc.excluded,
        "grid_badge": sc.grid_badge.value,
        "large_campus": sc.large_campus,
        "split_suggested": sc.split_suggested,
        "flags": sc.flags_pl,
        "detail": json.dumps(detail, ensure_ascii=False),
        "scored_at": scored_at,
        "wkb": wkb.dumps(s.geom),
        "profiles": json.dumps(to_jsonable(verdicts), ensure_ascii=False),
        "verdict_built": verdicts["built"].category.value,
        "verdict_container": verdicts["container"].category.value,
        "kind": item.kind,
        "grid60": item.grid60.value.value if item.grid60 else None,
    }


def write_sites(
    session: Session,
    scored: Sequence[ScoredSite],
    *,
    scored_at: datetime,
    powiat: str,
    profiles: ProfilesConfig | None = None,
) -> int:
    """Replace the sites of one powiat with this run's results; other powiats are kept.

    Runs inside the caller's transaction.
    """
    session.execute(text("DELETE FROM sites WHERE powiat = :powiat"), {"powiat": powiat})
    if scored:
        profiles = profiles or load_profiles()
        session.execute(_INSERT, [_row(item, scored_at, powiat, profiles) for item in scored])
    return len(scored)
