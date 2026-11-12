"""What changed in a powiat's kept sites between two searches: the rows `write_sites` adds to `site_history`.

Only kept standard sites are tracked (`kind = 'standard' AND NOT excluded`, what the list shows). A site that
becomes excluded counts as dropped, and one that stops being excluded as new.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

SCORE_DECIMALS = 1  # scores are compared rounded, so float noise never writes a row
_VERDICTS = ("verdict_built", "verdict_container")

_OLD = text(
    """
    SELECT site_id, score, verdict_built, verdict_container FROM sites
    WHERE powiat = :powiat AND kind = 'standard' AND NOT excluded
    """
)
_INSERT = text(
    """
    INSERT INTO site_history (
        site_id, event, powiat, area_ha, estimated_mw, score, verdict_built, verdict_container,
        prev_score, prev_verdict_built, prev_verdict_container, scored_at
    ) VALUES (
        :site_id, :event, :powiat, :area_ha, :estimated_mw, :score, :verdict_built, :verdict_container,
        :prev_score, :prev_verdict_built, :prev_verdict_container, :scored_at
    )
    """
)
# The gmina of a dropped site: its row is deleted right after, so it is looked up now.
_DROPPED = text(
    """
    INSERT INTO site_history (
        site_id, event, powiat, gmina, area_ha, estimated_mw, score, verdict_built, verdict_container,
        scored_at
    )
    SELECT s.site_id, 'dropped', s.powiat, g.name, s.area_ha, s.estimated_mw, s.score, s.verdict_built,
           s.verdict_container, :scored_at
    FROM sites s
    LEFT JOIN admin_units g ON g.level = 'gmina' AND ST_Covers(g.geom, ST_PointOnSurface(s.geom))
    WHERE s.powiat = :powiat AND s.site_id = ANY(:ids)
    """
)


def kept(row: Mapping[str, Any]) -> bool:
    return row["kind"] == "standard" and not row["excluded"]


def _score(value: float) -> float:
    return round(float(value), SCORE_DECIMALS)


def diff(
    old: Mapping[str, Any], new_rows: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Events for new and changed sites, and the ids that dropped. `old` maps site_id to the previous row."""
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in new_rows:
        if not kept(row):
            continue
        seen.add(row["site_id"])
        before = old.get(row["site_id"])
        event = {k: row[k] for k in ("site_id", "powiat", "area_ha", "estimated_mw", "score", *_VERDICTS)} | {
            "prev_score": None,
            "prev_verdict_built": None,
            "prev_verdict_container": None,
        }
        if before is None:
            events.append(event | {"event": "new"})
        elif _score(before["score"]) != _score(row["score"]) or any(before[v] != row[v] for v in _VERDICTS):
            events.append(
                event
                | {
                    "event": "changed",
                    "prev_score": before["score"],
                    "prev_verdict_built": before["verdict_built"],
                    "prev_verdict_container": before["verdict_container"],
                }
            )
    return events, sorted(set(old) - seen)


def record(
    session: Session, new_rows: Sequence[Mapping[str, Any]], *, powiat: str, scored_at: datetime
) -> None:
    """Write the events of one powiat's re-search. Call before the powiat's old rows are deleted.

    One indexed read of the powiat's kept rows; nothing is written when nothing changed.
    """
    old = {r["site_id"]: r for r in session.execute(_OLD, {"powiat": powiat}).mappings()}
    events, dropped = diff(old, new_rows)
    if events:
        session.execute(_INSERT, [{**e, "scored_at": scored_at} for e in events])
    if dropped:
        session.execute(_DROPPED, {"powiat": powiat, "ids": dropped, "scored_at": scored_at})
