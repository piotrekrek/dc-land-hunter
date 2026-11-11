"""The search queue ("Kolejka") for Claude: read it, and add or remove priority powiats.

The priority list is shared by the whole team, as in the app. Writes go through the app's own
`save_priority`, so its validation, its order (by `added_at`) and `added_by` apply unchanged.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.mcp.areas import resolve_area
from dclh_api.mcp.context import require_write_scope, run_db
from dclh_api.routers import ops, powiats
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.powiat_search import powiats_outside_aoi

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Adding or removing twice changes nothing, and a removal is undone by adding again.
EDIT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

NOTE = (
    "Kolejka priorytetów jest wspólna dla całego zespołu. Nocne wyszukiwanie (01:00) bierze do 8 powiatów: "
    "najpierw powiaty z priorytetem, które nie mają jeszcze udanego wyszukiwania, w kolejności dodania, "
    "potem te sprawdzane najdawniej. Po udanym wyszukiwaniu powiat z priorytetem wraca do zwykłej rotacji. "
    "next_up to pierwsze 4 z nocnego planu."
)
OUTSIDE = "Powiat {name} leży poza obszarem poszukiwań (na wschód od Wisły): nocny plan go pomija."

Powiaty = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=60)]],
    Field(
        min_length=1,
        max_length=20,
        description='Powiats by Polish name ("wrocławski", "powiat Leszno") or 4-digit TERYT code',
    ),
]

_SEARCH_JOBS = text(
    """
    SELECT j.id, j.status, j.payload->>'powiat' AS teryt, a.name, j.created_at, j.started_at
    FROM jobs j
    LEFT JOIN admin_units a ON a.level = 'powiat' AND a.teryt = j.payload->>'powiat'
    WHERE j.kind = 'search' AND j.status IN ('pending', 'running')
    ORDER BY j.id
    """
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def queue(session: Session) -> dict[str, Any]:
    listing = powiats.list_powiats(session)
    by_code = {p.teryt: p for p in listing.items}
    jobs = list(session.execute(_SEARCH_JOBS).mappings())
    busy = {j["teryt"] for j in jobs}
    saved = session.execute(
        text("SELECT teryt, added_by, added_at FROM powiat_priority ORDER BY added_at, teryt")
    ).mappings()

    def state(teryt: str) -> str:
        p = by_code.get(teryt)
        if p is None or not p.in_aoi:
            return "outside_aoi"
        if teryt in busy:
            return "in_progress"
        return "searched" if p.checked else "waiting"

    def named(teryt: str) -> dict[str, Any]:
        p = by_code.get(teryt)
        return {
            "teryt": teryt,
            "name": p.name if p else None,
            "voivodeship": p.voivodeship_name if p else None,
        }

    priority = []
    for row in saved:
        p = by_code.get(row["teryt"])
        priority.append(
            {
                **named(row["teryt"]),
                "state": state(row["teryt"]),
                "added_by": row["added_by"],
                "added_at": _iso(row["added_at"]),
                "last_ok_at": _iso(p.last_ok_at) if p else None,
                "last_attempt_at": _iso(p.last_attempt_at) if p else None,
                "last_status": p.status if p else None,
                "sites": p.sites if p else None,
                "error": p.error if p else None,
            }
        )
    failing = session.execute(ops._FAILING_POWIATS, {"chars": ops.ERROR_CHARS}).mappings()
    return {
        "note": NOTE,
        "priority": priority,
        "next_up": [{**named(t), "priority": by_code[t].priority} for t in listing.next_up],
        "in_progress": [
            {
                "job_id": j["id"],
                "teryt": j["teryt"],
                "name": j["name"],
                "status": j["status"],
                "queued_at": _iso(j["created_at"]),
                "started_at": _iso(j["started_at"]),
            }
            for j in jobs
        ],
        "failing": [
            {"teryt": f["teryt"], "name": f["name"], "failed_at": _iso(f["failed_at"]), "error": f["error"]}
            for f in failing
        ],
    }


def _resolve(session: Session, values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(resolve_area(session, "powiat", v)) for v in values))


def _saved(session: Session) -> list[str]:
    return list(session.scalars(text("SELECT teryt FROM powiat_priority ORDER BY added_at, teryt")))


def add(session: Session, user: UserRow, values: list[str]) -> dict[str, Any]:
    wanted = _resolve(session, values)
    outside = powiats_outside_aoi(session)
    for teryt in wanted:
        if teryt in outside:
            name = session.scalar(text("SELECT name FROM admin_units WHERE teryt = :t"), {"t": teryt})
            raise ToolError(OUTSIDE.format(name=name or teryt))
    saved = _saved(session)
    added = [t for t in wanted if t not in saved]
    _save(session, user, saved + added)
    return {"added": added, "already_in_queue": [t for t in wanted if t in saved], **queue(session)}


def remove(session: Session, user: UserRow, values: list[str]) -> dict[str, Any]:
    wanted = set(_resolve(session, values))
    saved = _saved(session)
    _save(session, user, [t for t in saved if t not in wanted])
    return {
        "removed": [t for t in saved if t in wanted],
        "not_in_queue": sorted(wanted - set(saved)),
        **queue(session),
    }


def _save(session: Session, user: UserRow, teryts: list[str]) -> None:
    try:
        powiats.save_priority(powiats.PriorityIn(teryts=teryts), session, user)
    except HTTPException as exc:  # an unknown code: the router's Polish message
        raise ToolError(str(exc.detail)) from exc


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def get_queue() -> dict[str, Any]:
        """The search queue (Kolejka): the team's priority powiats with their state (waiting, in_progress,
        searched, outside_aoi), the next powiats the nightly search takes, searches running or waiting now,
        and powiats whose latest search failed."""
        return await run_db(lambda session, _user: queue(session))

    @mcp.tool(annotations=EDIT)
    async def add_priority(powiaty: Powiaty) -> dict[str, Any]:
        """Give powiats priority in the search queue (the team's shared list). Use only on request.

        They go to the end of the priority list (several in one call: by code); ones already there keep
        their place. Returns the queue."""
        require_write_scope()
        return await run_db(lambda session, user: add(session, user, powiaty))

    @mcp.tool(annotations=EDIT)
    async def remove_priority(powiaty: Powiaty) -> dict[str, Any]:
        """Take powiats off the priority list (they stay in the normal rotation). Use only on request.

        Returns the queue."""
        require_write_scope()
        return await run_db(lambda session, user: remove(session, user, powiaty))
