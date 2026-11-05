"""The few tools that change something. Each acts as the token's user and checks the write scope first.

They call the same router functions as the app (so its limits and Polish messages apply unchanged) and are
deliberately small: watching a site is the user's own list, and a parcel check is queued work, not a deletion.
Nothing here touches the deal pipeline or the powiat priority list.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from dclh_api.mcp.context import require_write_scope, run_db, site_url
from dclh_api.routers import checks, sites

# Watching twice changes nothing, and watching is the user's own list.
WATCH = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Queues background work (a second call queues a second check) that asks public services about the parcel.
CHECK = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

ONE_PARCEL = (
    "Podaj dokładnie jedną działkę, najlepiej pełnym identyfikatorem (na przykład 142104_2.0013.562/2)."
)


def register(mcp: FastMCP, public_url: str) -> None:
    @mcp.tool(annotations=WATCH)
    async def watch_site(site_id: str) -> dict[str, Any]:
        """Put a site on the user's watch list (the star in the app). Safe to repeat."""
        require_write_scope()
        state = await run_db(lambda session, user: sites.watch_site(site_id, session, user))
        return {"site_id": site_id, "watched": state.watched, "url": site_url(public_url, site_id)}

    @mcp.tool(annotations=WATCH)
    async def unwatch_site(site_id: str) -> dict[str, Any]:
        """Take a site off the user's watch list. Safe to repeat."""
        require_write_scope()
        state = await run_db(lambda session, user: sites.unwatch_site(site_id, session, user))
        return {"site_id": site_id, "watched": state.watched, "url": site_url(public_url, site_id)}

    @mcp.tool(annotations=CHECK)
    async def check_parcel(
        parcel_id: Annotated[
            str, Field(min_length=1, max_length=100, description="Parcel id, e.g. 142104_2.0013.562/2")
        ],
    ) -> dict[str, Any]:
        """Queue a check of one parcel ("Sprawdź działkę"): score, constraints and what is left to verify.

        It runs in the background and takes a few minutes. Read the result with `get_check(check_id)`."""
        require_write_scope()

        def queue(session: Any, user: Any) -> dict[str, Any]:
            # Ask ULDK first, so that a parcel that does not exist is refused now and not after a long job.
            found = checks.resolve(checks.ResolveIn(text=parcel_id), session)
            if found.issues:
                raise ToolError(found.issues[0].message_pl)
            if len(found.items) != 1:
                raise ToolError(ONE_PARCEL)
            parcel = found.items[0]
            created = checks.start_check(checks.CheckIn(parcels=[parcel.parcel_id]), session, user)
            return {
                "check_id": created.id,
                "status": "pending",
                "url": f"{public_url.rstrip('/')}/sprawdz-dzialke/{created.id}",
                "parcel": {
                    "parcel_id": parcel.parcel_id,
                    "commune": parcel.commune,
                    "obreb": parcel.obreb,
                    "county": parcel.county,
                    "voivodeship": parcel.voivodeship,
                    "area_ha": parcel.area_ha,
                    "in_aoi": parcel.in_aoi,
                },
            }

        return await run_db(queue)
