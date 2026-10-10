"""One scoring run: candidate sites and loaded layers → scored sites → the `sites` table (brief 5, step 5).

A run replaces the whole table, so a site that disappeared from the input disappears from the list too.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from shapely import wkb
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.config import ScoringConfig
from dclh_pipeline.model.features import SiteLayers, build_site_features
from dclh_pipeline.model.sites import Site
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.types import SiteFeatures, SiteScore


@dataclass(frozen=True)
class ScoredSite:
    site: Site
    features: SiteFeatures
    score: SiteScore


def score_candidates(
    sites: Iterable[Site],
    layers: SiteLayers,
    cfg: ScoringConfig,
    *,
    parcels_as_of: datetime | None = None,
) -> list[ScoredSite]:
    """Compute features and score every site. Excluded sites are kept, to show them greyed out."""
    out: list[ScoredSite] = []
    for site in sites:
        features = build_site_features(site, layers, cfg, parcels_as_of=parcels_as_of)
        out.append(ScoredSite(site=site, features=features, score=score_site(features, cfg)))
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
        site_id, zone_id, parcel_ids, area_ha, compactness, estimated_mw, score, upside_score, completeness,
        excluded, grid_badge, large_campus, split_suggested, flags, detail, scored_at, geom
    ) VALUES (
        :site_id, :zone_id, :parcel_ids, :area_ha, :compactness, :estimated_mw, :score, :upside_score,
        :completeness, :excluded, :grid_badge, :large_campus, :split_suggested, :flags,
        CAST(:detail AS jsonb),
        :scored_at, ST_SetSRID(ST_GeomFromWKB(:wkb), 2180)
    )
    """
)


def _row(item: ScoredSite, scored_at: datetime) -> dict[str, Any]:
    s, sc = item.site, item.score
    detail = {"score": to_jsonable(sc), "features": to_jsonable(item.features)}
    return {
        "site_id": s.site_id,
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
    }


def write_sites(session: Session, scored: Sequence[ScoredSite], *, scored_at: datetime) -> int:
    """Replace the `sites` table with this run's results. Runs inside the caller's transaction."""
    session.execute(text("DELETE FROM sites"))
    if scored:
        session.execute(_INSERT, [_row(item, scored_at) for item in scored])
    return len(scored)
