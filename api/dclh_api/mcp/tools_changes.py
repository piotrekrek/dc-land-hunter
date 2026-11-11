"""`what_changed`: new and dropped sites, verdict changes, score jumps and first-time powiats in a period.

It reads `site_history` (migration 0018), which `write_sites` fills when a powiat search changes a kept site.
The history starts on the deploy date: older changes are not known, and the answer says so.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.mcp import areas
from dclh_api.mcp.context import run_db
from dclh_api.mcp.search import PROFILES, Profile, _rows, site_line

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

SCORE_JUMP = 5.0  # points; a smaller move of the score is not reported
DEFAULT_DAYS = 7
MAX_DAYS = 90
DEFAULT_LIMIT = 20
MAX_LIMIT = 50  # per category
WARSAW = ZoneInfo("Europe/Warsaw")
_RELATIVE = re.compile(r"^(\d{1,3})\s*([dw])$", re.IGNORECASE)
_SHOWN = ("site_id", "url", "gmina", "powiat", "voivodeship", "area_ha", "estimated_mw", "score", "verdicts")

HISTORY_NOTE = (
    "Historia zmian jest prowadzona od {start}. Wcześniejszych zmian nie ma w bazie. Zmiana to nowy wynik "
    "nocnego wyszukiwania w danym powiecie: lokalizacja pojawiła się, zniknęła albo zmieniła ocenę."
)
EARLIER_NOTE = (
    "Okres zaczyna się przed początkiem historii ({start}); wyniki obejmują tylko czas od tej daty."
)
CLAMPED_NOTE = f"Okres skrócono do {MAX_DAYS} dni (maksimum)."
FIRST_NOTE = (
    "Powiat przeszukany po raz pierwszy: jego lokalizacje nie są liczone jako nowe (brak porównania)."
)
DROPPED_NOTE = (
    "Lokalizacje, które zniknęły po ponownym wyszukiwaniu (inny przebieg działek, wykluczenie albo zmiana "
    "danych). Ich karta już nie istnieje, więc nie ma url."
)


def parse_since(raw: str, now: datetime) -> tuple[datetime, list[str]]:
    """`7d`, `2w` or an ISO date/time (a date starts at midnight, Warsaw time). Over 90 days is cut to 90."""
    value = raw.strip()
    relative = _RELATIVE.match(value)
    if relative:
        days = int(relative.group(1)) * (7 if relative.group(2).lower() == "w" else 1)
        if days < 1:
            raise ToolError("Okres musi mieć co najmniej 1 dzień.")
        since = now - timedelta(days=days)
    else:
        try:
            since = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ToolError(
                f"Nie rozumiem okresu „{raw}”. Podaj datę (2026-11-04) albo liczbę dni, np. „7d”, „14d”."
            ) from exc
        if since.tzinfo is None:
            since = since.replace(tzinfo=WARSAW)
    if since > now:
        raise ToolError("Początek okresu jest w przyszłości.")
    limit = now - timedelta(days=MAX_DAYS)
    if since < limit:
        return limit, [CLAMPED_NOTE]
    return since, []


def _net(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One site's events in order → what happened over the period, or None when it came and went again.

    `before` is the state at the start of the period (None: the site was not there), `after` the state now.
    """
    first, last = events[0], events[-1]

    def state(e: dict[str, Any]) -> dict[str, Any]:
        return {
            "score": e["score"],
            "built": e["verdict_built"],
            "container": e["verdict_container"],
        }

    before: dict[str, Any] | None = None
    if first["event"] == "changed":
        before = {
            "score": first["prev_score"],
            "built": first["prev_verdict_built"],
            "container": first["prev_verdict_container"],
        }
    elif first["event"] == "dropped":
        before = state(first)
    after = None if last["event"] == "dropped" else state(last)
    if before is None and after is None:
        return None
    return {"event": last, "before": before, "after": after}


def _verdict_changes(net: dict[str, Any], profile: Profile | None) -> dict[str, dict[str, str]]:
    before, after = net["before"], net["after"]
    return {
        p: {"from": before[p], "to": after[p]}
        for p in ((profile,) if profile else PROFILES)
        if before[p] != after[p]
    }


def _first_searched(
    session: Session, since: datetime, where: str, params: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT h.powiat AS teryt, a.name, r.last_ok_at FROM site_history h "
            "LEFT JOIN admin_units a ON a.level = 'powiat' AND a.teryt = h.powiat "
            "LEFT JOIN search_runs r ON r.teryt = h.powiat "
            f"WHERE true {where} GROUP BY h.powiat, a.name, r.last_ok_at "
            "HAVING min(h.scored_at) >= :since AND bool_or(h.event = 'new') ORDER BY h.powiat"
        ),
        params | {"since": since},
    ).mappings()
    return {r["teryt"]: dict(r) for r in rows}


def changes(
    session: Session,
    public_url: str,
    *,
    since: str,
    voivodeship: str | None,
    powiat: str | None,
    profile: Profile | None,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since_at, notes = parse_since(since, now)
    limit = min(max(limit, 1), MAX_LIMIT)
    voivodeship_code = areas.resolve_area(session, "voivodeship", voivodeship)
    powiat_code = areas.resolve_area(session, "powiat", powiat, within=voivodeship_code)
    area_sql, params = "", {}
    if voivodeship_code is not None:
        area_sql += " AND left(h.powiat, 2) = :voivodeship"
        params["voivodeship"] = voivodeship_code
    if powiat_code is not None:
        area_sql += " AND h.powiat = :powiat"
        params["powiat"] = powiat_code
    try:
        start = session.execute(
            text(
                "SELECT coalesce(min(recorded_at) FILTER (WHERE event = 'baseline'), min(recorded_at)) "
                "FROM site_history"
            )
        ).scalar_one()
        events = [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT h.* FROM site_history h WHERE h.event <> 'baseline' AND h.scored_at >= :since"
                    f"{area_sql} ORDER BY h.site_id, h.scored_at, h.id"
                ),
                params | {"since": since_at},
            ).mappings()
        ]
        first_searched = _first_searched(session, since_at, area_sql, params)
    except SQLAlchemyError as exc:
        raise ToolError("Baza danych jest niedostępna.") from exc

    by_site: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        by_site.setdefault(e["site_id"], []).append(e)
    new, dropped, verdict_changed, score_jumped = [], [], [], []
    for site_id, site_events in by_site.items():
        net = _net(site_events)
        if net is None:
            continue
        last, before, after = net["event"], net["before"], net["after"]
        if before is None:
            if last["powiat"] not in first_searched:
                new.append((site_id, last, None))
        elif after is None:
            dropped.append((site_id, last, None))
        else:
            if _verdict_changes(net, profile):
                verdict_changed.append((site_id, last, net))
            if abs(after["score"] - before["score"]) >= SCORE_JUMP:
                score_jumped.append((site_id, last, net))
    first_counts: dict[str, int] = {}
    for site_events in by_site.values():
        net = _net(site_events)
        if net and net["before"] is None and net["event"]["powiat"] in first_searched:
            first_counts[net["event"]["powiat"]] = first_counts.get(net["event"]["powiat"], 0) + 1

    def top(items: list[tuple[str, dict[str, Any], Any]]) -> list[tuple[str, dict[str, Any], Any]]:
        return sorted(items, key=lambda i: (-i[1]["score"], i[0]))[:limit]

    shown = {
        c: top(items) for c, items in (("new", new), ("verdicts", verdict_changed), ("score", score_jumped))
    }
    ids = list({i[0] for items in shown.values() for i in items})
    lines = _current_lines(session, public_url, ids, profile)
    names = _powiat_names(session, {i[1]["powiat"] for i in top(dropped)})

    def line(item: tuple[str, dict[str, Any], Any]) -> dict[str, Any] | None:
        current = lines.get(item[0])
        return None if current is None else {k: current[k] for k in _SHOWN}

    def changed_line(item: tuple[str, dict[str, Any], Any]) -> dict[str, Any] | None:
        base = line(item)
        if base is None:
            return None
        net = item[2]
        before, after = net["before"], net["after"]
        return base | {
            "verdict_changes": _verdict_changes(net, profile),
            "score_change": {
                "from": round(before["score"], 1),
                "to": round(after["score"], 1),
                "delta": round(after["score"] - before["score"], 1),
            },
        }

    def dropped_line(item: tuple[str, dict[str, Any], Any]) -> dict[str, Any]:
        e = item[1]
        voivodeship_name, powiat_name = names.get(e["powiat"], (None, None))
        return {
            "site_id": item[0],
            "gmina": e["gmina"],
            "powiat": powiat_name,
            "voivodeship": voivodeship_name,
            "area_ha": round(e["area_ha"], 2),
            "estimated_mw": round(e["estimated_mw"], 1),
            "score": round(e["score"], 1),
            "verdicts": _verdicts(e),
            "dropped_at": e["scored_at"].isoformat(),
        }

    start_text = start.astimezone(WARSAW).strftime("%d.%m.%Y") if start else None
    if start is not None and since_at < start:
        notes.append(EARLIER_NOTE.format(start=start_text))
    return {
        "since": since_at.isoformat(),
        "history_since": start.isoformat() if start else None,
        "note": HISTORY_NOTE.format(start=start_text or "dnia wdrożenia"),
        "notes": notes,
        "score_jump_points": SCORE_JUMP,
        "counts": {
            "new": len(new),
            "dropped": len(dropped),
            "verdict_changes": len(verdict_changed),
            "score_jumps": len(score_jumped),
            "first_searched_powiaty": len(first_searched),
        },
        "new_sites": [x for x in map(line, shown["new"]) if x],
        "dropped_sites": [dropped_line(i) for i in top(dropped)],
        "dropped_note": DROPPED_NOTE,
        "verdict_changes": [x for x in map(changed_line, shown["verdicts"]) if x],
        "score_jumps": [x for x in map(changed_line, shown["score"]) if x],
        "first_searched_powiaty": [
            {
                "teryt": t,
                "name": r["name"],
                "searched_at": r["last_ok_at"].isoformat() if r["last_ok_at"] else None,
                "sites": first_counts.get(t, 0),
            }
            for t, r in first_searched.items()
        ],
        "first_searched_note": FIRST_NOTE,
    }


def _verdicts(e: dict[str, Any]) -> dict[str, str] | None:
    built, container = e["verdict_built"], e["verdict_container"]
    return {"built": built, "container": container} if built and container else None


def _current_lines(
    session: Session, public_url: str, ids: list[str], profile: Profile | None
) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = _rows(
        session, "WITH page AS (SELECT site_id, 0 AS sv FROM sites WHERE site_id = ANY(:ids))", {"ids": ids}
    )
    return {r["site_id"]: site_line(r, public_url, profile=profile) for r in rows}


def _powiat_names(session: Session, teryts: set[str]) -> dict[str, tuple[str | None, str | None]]:
    """powiat code → (voivodeship name, powiat name)."""
    if not teryts:
        return {}
    rows = session.execute(
        text(
            "SELECT p.teryt, p.name AS powiat, v.name AS voivodeship FROM admin_units p "
            "LEFT JOIN admin_units v ON v.level = 'voivodeship' AND v.teryt = left(p.teryt, 2) "
            "WHERE p.level = 'powiat' AND p.teryt = ANY(:t)"
        ),
        {"t": sorted(teryts)},
    ).mappings()
    return {r["teryt"]: (r["voivodeship"], r["powiat"]) for r in rows}


def register(mcp: FastMCP, public_url: str) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def what_changed(
        since: Annotated[
            str,
            Field(
                max_length=40,
                description='ISO date ("2026-11-04") or relative ("7d", "14d", "2w"); max 90 days',
            ),
        ] = f"{DEFAULT_DAYS}d",
        voivodeship: Annotated[str | None, Field(max_length=60, description="Name or TERYT code")] = None,
        powiat: Annotated[str | None, Field(max_length=60, description="Name or TERYT code")] = None,
        profile: Annotated[
            Literal["built", "container"] | None,
            Field(description="Limits verdict changes and the verdicts shown to this investor profile"),
        ] = None,
        limit: Annotated[int, Field(description="Sites per category; capped at 50")] = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """What changed since a date or N days ago ("what is new this week?"): sites that appeared or
        disappeared after the nightly search, verdict changes (from/to), score jumps of 5 points or more, and
        powiats searched for the first time. Each site has url, gmina, powiat, area, estimated MW, score and
        verdicts. `counts` gives the totals per category; `history_since` is the date the history starts."""
        return await run_db(
            lambda session, _user: changes(
                session,
                public_url,
                since=since,
                voivodeship=voivodeship,
                powiat=powiat,
                profile=profile,
                limit=limit,
            )
        )
