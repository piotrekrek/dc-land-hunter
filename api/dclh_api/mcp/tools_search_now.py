"""Queue a powiat search right now, instead of waiting for the nightly run (01:00).

Searches are heavy (long calls to public services, disk), so this is for admins and capped per UTC day.
It enqueues the same `search` job as the planner, through `jobs.enqueue`, so the worker handles it
identically (including its disk guard). A manual run has no `not_after`: that window only stops a nightly
run in the morning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.mcp.areas import resolve_area
from dclh_api.mcp.context import require_write_scope, run_db
from dclh_api.mcp.tools_queue import OUTSIDE
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.jobs import enqueue
from dclh_pipeline.powiat_search import powiats_outside_aoi

MANUAL_SEARCHES_PER_DAY = 3  # per UTC day, across all users
# Not idempotent: each call queues one more heavy search. Asks public services, so open world.
HEAVY = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

NOTE = (
    "Wyszukiwanie czeka w kolejce na workera i może trwać długo. Śledź je przez get_queue: "
    "widać je w in_progress, a po zakończeniu powiat ma świeżą datę ostatniego wyszukiwania."
)

# A manual search is one requested by a user (the scheduler, the planner and memory retries use other labels).
_MANUAL_TODAY = text(
    """
    SELECT count(*) FROM jobs j
    WHERE j.kind = 'search' AND j.created_at >= :day_start
      AND EXISTS (SELECT 1 FROM users u WHERE u.email = j.requested_by)
    """
)
_BUSY = text(
    """
    SELECT id, status FROM jobs
    WHERE kind = 'search' AND status IN ('pending', 'running') AND payload->>'powiat' = :t
    ORDER BY id LIMIT 1
    """
)


def queue_search(session: Session, user: UserRow, value: str) -> dict[str, Any]:
    if user.role != "admin":
        raise ToolError("Natychmiastowe wyszukiwanie może zlecić tylko administrator.")
    teryt = str(resolve_area(session, "powiat", value))
    name = session.scalar(
        text("SELECT name FROM admin_units WHERE level = 'powiat' AND teryt = :t"), {"t": teryt}
    )
    if teryt in powiats_outside_aoi(session):
        raise ToolError(OUTSIDE.format(name=name or teryt))
    # One at a time, so two calls cannot both pass the checks below. Released at commit.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('dclh_search_now'))"))
    busy = session.execute(_BUSY, {"t": teryt}).first()
    if busy is not None:
        state = "trwa" if busy.status == "running" else "czeka w kolejce"
        raise ToolError(f"Powiat {name or teryt} ma już wyszukiwanie: zadanie {busy.id} {state}.")
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = int(session.scalar(_MANUAL_TODAY, {"day_start": day_start}) or 0)
    if used >= MANUAL_SEARCHES_PER_DAY:
        raise ToolError(
            f"Osiągnięto dzienny limit ręcznych wyszukiwań ({MANUAL_SEARCHES_PER_DAY} na dobę UTC, "
            "dla całego zespołu). Spróbuj jutro albo poczekaj na nocne wyszukiwanie."
        )
    job = enqueue(session, "search", {"powiat": teryt}, requested_by=user.email)
    session.commit()
    return {
        "job_id": job.id,
        "teryt": teryt,
        "powiat": name,
        "status": "pending",
        "requested_by": user.email,
        "searches_left_today": MANUAL_SEARCHES_PER_DAY - used - 1,
        "note": NOTE,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=HEAVY)
    async def search_now(
        powiat: Annotated[
            str,
            Field(
                min_length=1,
                max_length=60,
                description='Powiat by Polish name ("wrocławski") or 4-digit TERYT',
            ),
        ],
    ) -> dict[str, Any]:
        """Queue a search of one powiat right now instead of the nightly run. Administrators only; use only
        on explicit request. Heavy (calls public services for a long time, uses disk) and capped per day for
        the whole team. Refused for a powiat outside the area or one already being searched. Follow the
        job with get_queue (in_progress)."""
        require_write_scope()
        return await run_db(lambda session, user: queue_search(session, user, powiat))
