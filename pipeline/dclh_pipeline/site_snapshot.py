"""Read-only snapshots of scored sites and the diff of two snapshots (scoring regression check)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

PROFILES = ("built", "container")

_SQL = """
SELECT s.site_id, s.score, s.upside_score, s.area_ha, s.estimated_mw, s.flags,
       s.verdict_built, s.verdict_container,
       s.detail -> 'score' -> 'categories' AS categories,
       s.detail -> 'features' -> 'dist_gpz110_km' ->> 'value' AS gpz_km,
       gpz.name AS gpz_name
FROM sites s
LEFT JOIN LATERAL (
    SELECT name FROM substations WHERE cls = 'GPZ_110_SN' ORDER BY geom <-> s.geom LIMIT 1
) gpz ON true
WHERE s.site_id = ANY(:ids)
   OR (CAST(:gmina AS text) IS NOT NULL AND EXISTS (
        SELECT 1 FROM admin_units g
        WHERE g.level = 'gmina' AND g.teryt = :gmina AND ST_Covers(g.geom, ST_PointOnSurface(s.geom))))
ORDER BY s.site_id
"""


def _category_points(raw: Any) -> dict[str, float]:
    """`detail.score.categories` is a list of {category, points, ...}; tolerate a plain mapping too."""
    if isinstance(raw, dict):
        return {str(k): round(float(v), 1) for k, v in raw.items() if isinstance(v, int | float)}
    return {str(c["category"]): round(float(c["points"]), 1) for c in raw or []}


def snapshot_sites(session: Session, site_ids: list[str], gmina: str | None) -> dict[str, dict[str, Any]]:
    """Site id -> snapshot entry, for the given ids and/or every site in a gmina. Only reads."""
    rows = session.execute(text(_SQL), {"ids": site_ids, "gmina": gmina}).mappings()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        km = None if r["gpz_km"] is None else round(float(r["gpz_km"]), 2)
        out[r["site_id"]] = {
            "score": round(float(r["score"]), 1),
            "upside": round(float(r["upside_score"]), 1),
            "verdicts": {"built": r["verdict_built"], "container": r["verdict_container"]},
            "categories": _category_points(r["categories"]),
            "flags": list(r["flags"] or []),
            "nearest_gpz": {"name": r["gpz_name"], "distance_km": km},
            "estimated_mw": round(float(r["estimated_mw"]), 1),
            "area_ha": round(float(r["area_ha"]), 2),
        }
    return out


@dataclass
class SnapshotDiff:
    only_a: list[str] = field(default_factory=list)
    only_b: list[str] = field(default_factory=list)
    verdict_changes: dict[str, dict[str, tuple[str | None, str | None]]] = field(default_factory=dict)
    score_moves: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.only_a or self.only_b or self.verdict_changes or self.score_moves)


def diff_snapshots(
    a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], threshold: float = 5.0
) -> SnapshotDiff:
    """Sites whose verdict changed in any profile or whose score moved by more than `threshold` points."""
    diff = SnapshotDiff(only_a=sorted(a.keys() - b.keys()), only_b=sorted(b.keys() - a.keys()))
    for site_id in sorted(a.keys() & b.keys()):
        va, vb = a[site_id].get("verdicts") or {}, b[site_id].get("verdicts") or {}
        changed = {p: (va.get(p), vb.get(p)) for p in sorted(va.keys() | vb.keys()) if va.get(p) != vb.get(p)}
        if changed:
            diff.verdict_changes[site_id] = changed
        before, after = a[site_id]["score"], b[site_id]["score"]
        if abs(after - before) > threshold:
            diff.score_moves[site_id] = (before, after)
    return diff
