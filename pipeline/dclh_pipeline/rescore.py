"""Fill `dist_telecom_node_km` on every stored site and move its score by that fact alone (no new search).

A search computes every fact, but the nightly run reaches each powiat only every few weeks. When the UKE fibre
node layer arrives (`dclh fetch uke`), this backfills the one fact from the stored site geometry.

The stored scores were computed by the rules of their search day, and some rules have changed since (on
20.11.2026 about one site in five would move on `grid_node` alone). A full rescore would slip those changes
in unannounced, so only the telecom effect is applied: the site is scored twice by today's rules, with and
without the new distance, and the difference is added to the stored score, upside and completeness. In
`detail.score` only the `conn_telecom` item and the connectivity category change. Running it again changes
nothing unless the node layer changed. The next search of the powiat rescores everything as usual.

When the telecom thresholds change, `previous` names the old ones: the site is then scored by the old and the
new thresholds (both with the distance), and the difference is added. A site whose stored telecom item already
has the new points is left as it is, so a second run with the same `previous` changes nothing.

Nothing goes to `site_history`: the site did not change, a data source was added, and 225k score changes would
bury the weekly "what changed" feed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text

from dclh_pipeline.config import ProfilesConfig, ScoringConfig, load_profiles, load_scoring, load_sources
from dclh_pipeline.db import session_scope
from dclh_pipeline.model.features import Layer, telecom_node_km_fact
from dclh_pipeline.profiles import features_from_detail, profile_verdicts
from dclh_pipeline.scoring.engine import score_site
from dclh_pipeline.scoring.run import small_site_config, to_jsonable
from dclh_pipeline.scoring.types import SiteScore
from dclh_pipeline.settings import get_settings
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.uke import telecom_node_layer

PAGE = 2000  # sites per page and transaction
KEY, CATEGORY = "conn_telecom", "connectivity"

_UPDATE = text(
    """
    UPDATE sites SET
        score = :score, upside_score = :upside_score, completeness = :completeness,
        detail = jsonb_set(jsonb_set(detail, '{score}', CAST(:score_json AS jsonb)),
                           '{features}', CAST(:features_json AS jsonb)),
        profiles = CAST(:profiles AS jsonb), verdict_built = :verdict_built,
        verdict_container = :verdict_container
    WHERE site_id = :site_id
    """
)


def patch_site(
    stored_features: dict[str, Any],
    stored_score: dict[str, Any],
    geom: BaseGeometry,
    layer: Layer,
    cfg: ScoringConfig,
    profiles: ProfilesConfig,
    previous: ScoringConfig | None = None,
) -> dict[str, Any]:
    """The new stored values of one site: features with the node distance, the score moved by its effect.

    `previous`: the rules the stored telecom item was scored by, when only its thresholds changed since.
    """
    base = features_from_detail(stored_features)
    features = replace(base, dist_telecom_node_km=telecom_node_km_fact(geom, layer))
    after = score_site(features, cfg)
    new_item = next(to_jsonable(i) for i in after.items if i.key == KEY)
    old_item = next((i for i in stored_score["items"] if i["key"] == KEY), None)
    if previous is not None and old_item is not None and _same_points(old_item, new_item):
        previous = None  # already on the new thresholds: compare like with like, so the delta is zero
    before = score_site(base, previous) if previous is not None else score_site(base, cfg)
    score = dict(stored_score)
    for name in ("score", "upside_score"):
        score[name] = _clip(stored_score[name] + getattr(after, name) - getattr(before, name), 0.0, 100.0)
    score["upside_score"] = max(score["upside_score"], score["score"])
    score["completeness"] = _clip(
        stored_score["completeness"] + after.completeness - before.completeness, 0.0, 1.0
    )
    items = [i for i in stored_score["items"] if i["key"] != KEY]
    at = next((n for n, i in enumerate(stored_score["items"]) if i["key"] == KEY), len(items))
    score["items"] = [*items[:at], new_item, *items[at:]]
    score["categories"] = [_moved(c, before, after) for c in stored_score["categories"]]
    verdicts = profile_verdicts(features, profiles)
    return {
        "score": score["score"],
        "upside_score": score["upside_score"],
        "completeness": score["completeness"],
        "score_json": json.dumps(score, ensure_ascii=False),
        "features_json": json.dumps(to_jsonable(features), ensure_ascii=False),
        "profiles": json.dumps(to_jsonable(verdicts), ensure_ascii=False),
        "verdict_built": verdicts["built"].category.value,
        "verdict_container": verdicts["container"].category.value,
    }


def _moved(category: dict[str, Any], before: SiteScore, after: SiteScore) -> dict[str, Any]:
    if category["category"] != CATEGORY:
        return category
    old = next(c for c in before.categories if c.category == CATEGORY)
    new = next(c for c in after.categories if c.category == CATEGORY)
    return category | {
        "points": category["points"] + new.points - old.points,
        "upside_points": category["upside_points"] + new.upside_points - old.upside_points,
    }


def _same_points(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(abs(a[k] - b[k]) < 1e-9 for k in ("points", "upside_points"))


def with_telecom_thresholds(cfg: ScoringConfig, full_km: float, zero_km: float) -> ScoringConfig:
    """`cfg` with other telecom thresholds: the rules a site was scored by before they changed."""
    conn = cfg.connectivity.model_copy(
        update={"telecom_node_km_full": full_km, "telecom_node_km_zero": zero_km}
    )
    return cfg.model_copy(update={"connectivity": conn})


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rescore_telecom(previous: tuple[float, float] | None = None) -> int:
    """Backfill the fibre node distance on every stored site. Returns the number of sites.

    `previous`: the old (full, zero) telecom thresholds in km, after a threshold change.
    Raises when no good `uke` snapshot exists, so a missing layer never wipes a distance a search stored.
    """
    layer = telecom_node_layer(SnapshotStore(get_settings().raw_dir), load_sources().get("uke"))
    if layer is None:
        raise ValueError("no good uke snapshot; run `dclh fetch uke` first")
    cfg, profiles = load_scoring(), load_profiles()
    small_cfg = small_site_config(cfg)
    old = old_small = None
    if previous is not None:
        old, old_small = (with_telecom_thresholds(c, *previous) for c in (cfg, small_cfg))
    done, last = 0, ""
    while True:
        with session_scope() as session:
            rows = session.execute(
                text(
                    "SELECT site_id, kind, detail -> 'features', detail -> 'score', ST_AsBinary(geom) "
                    "FROM sites WHERE site_id > :last ORDER BY site_id LIMIT :n"
                ),
                {"last": last, "n": PAGE},
            ).all()
            updates = [
                {"site_id": site_id}
                | patch_site(
                    features,
                    score,
                    wkb.loads(bytes(geom)),
                    layer,
                    small_cfg if kind == "small" else cfg,
                    profiles,
                    old_small if kind == "small" else old,
                )
                for site_id, kind, features, score, geom in rows
            ]
            if updates:
                session.execute(_UPDATE, updates)
        if not rows:
            break
        done, last = done + len(rows), rows[-1][0]
    return done
