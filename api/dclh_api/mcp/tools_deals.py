"""Deal pipeline tools: move a site to a stage and take it out again. They call the app's own endpoints
(`put_project`, `delete_project`), so the stage names, the 404 wording and the note handling are the app's.

The owner note is personal data (brief 9.6): it is never returned here."""

from __future__ import annotations

from typing import Any, get_args

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from dclh_api.mcp.context import require_write_scope, run_db, site_url
from dclh_api.routers import sites
from dclh_api.schemas import ProjectBody, ProjectStage

STAGES: tuple[str, ...] = get_args(ProjectStage)

SET_STAGE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
# Deletes the project card with the owner's private note.
REMOVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def register(mcp: FastMCP, public_url: str) -> None:
    @mcp.tool(annotations=SET_STAGE)
    async def set_stage(site_id: str, stage: str) -> dict[str, Any]:
        """Move a site to a deal pipeline stage, creating its card if there is none. Use only on request.

        Stages: new, watching, analysis, owner_contact, negotiation, rejected, secured.
        An existing owner note on the card is kept. Safe to repeat."""
        require_write_scope()
        if stage not in STAGES:
            raise ToolError(f"Nieznany etap „{stage}”. Dostępne etapy: {', '.join(STAGES)}.")
        # Only the stage is sent, so the app keeps the stored note.
        state = await run_db(
            lambda session, user: sites.put_project(site_id, ProjectBody(stage=stage), session, user)
        )
        return {"site_id": site_id, "stage": state.stage, "url": site_url(public_url, site_id)}

    @mcp.tool(annotations=REMOVE)
    async def remove_from_pipeline(site_id: str) -> dict[str, Any]:
        """Remove a site from the deal pipeline. DESTRUCTIVE: the project card is deleted together with the
        owner's private note, and that cannot be undone. Ask the user to confirm first; use only on request.
        Safe to repeat: with no card nothing happens."""
        require_write_scope()
        await run_db(lambda session, _user: sites.delete_project(site_id, session))
        return {"site_id": site_id, "in_pipeline": False, "url": site_url(public_url, site_id)}
