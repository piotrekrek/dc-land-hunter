"""Read-only tools. Each calls the router function behind the matching screen, so Claude sees the UI's data.

Results stay small on purpose (Claude's context is scarce): lists are capped, and geometry, the map block
and the project note never leave the server. `project_cards.owner_note` may hold personal data (brief 9.6).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import text

from dclh_api.mcp.context import GRID_NOTE, run_db, site_url
from dclh_api.routers import checks, config, powiats, sites
from dclh_api.routers import coverage as coverage_router
from dclh_api.schemas import SiteSummary

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

MAX_SITES = 50  # the routers' own cap is 200; a page that big would fill Claude's context
SITE_DETAIL_DROPPED = {"geometry", "map", "project_note"}

PowiatCode = Annotated[str | None, Field(pattern=r"^\d{4}$", description="Powiat TERYT, 4 digits, e.g. 2208")]
VoivodeshipCode = Annotated[str | None, Field(pattern=r"^\d{2}$", description="Voivodeship TERYT, 2 digits")]


def _site_line(site: SiteSummary, public_url: str) -> dict[str, Any]:
    return {
        "site_id": site.site_id,
        "url": site_url(public_url, site.site_id),
        "gmina": site.gmina,
        "powiat": site.powiat,
        "voivodeship": site.voivodeship,
        "area_ha": round(site.area_ha, 2),
        "estimated_mw": round(site.estimated_mw, 1),
        "score": round(site.score, 1),
        "verdicts": site.profile_categories,
        "grid_badge": site.grid_badge,
        "flags": site.flags,
    }


def register(mcp: FastMCP, public_url: str) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def search_sites(
        powiat: PowiatCode = None,
        voivodeship: VoivodeshipCode = None,
        min_mw: Annotated[float, Field(ge=0, description="Minimum estimated MW (area x density)")] = 60,
        min_ha: Annotated[float, Field(ge=0)] = 0,
        profile: Annotated[
            Literal["built", "container"] | None,
            Field(description="Investor profile that `hide_red` and the verdicts refer to"),
        ] = None,
        hide_red: Annotated[
            bool, Field(description="Drop sites `profile` rejects (needs `profile`)")
        ] = False,
        sort: Literal["score", "upside", "area", "mw"] = "score",
        limit: Annotated[int, Field(description="Sites per page; capped at 50")] = 20,
        cursor: Annotated[str | None, Field(description="`next_cursor` of the previous page")] = None,
    ) -> dict[str, Any]:
        """Ranked candidate sites of 10 ha or more, best first. Use `cursor` to get the next page."""
        page = await run_db(
            lambda session, _user: sites.list_sites(
                session=session,
                sort=sort,
                order="desc",
                min_mw=min_mw,
                min_ha=min_ha,
                include_excluded=False,
                powiat=powiat,
                voivodeship=voivodeship,
                profile=profile,
                hide_red=hide_red,
                limit=min(max(limit, 1), MAX_SITES),
                offset=0,
                cursor=cursor,
            )
        )
        return {
            "total": page.total,
            "next_cursor": page.next_cursor,
            "sites": [_site_line(s, public_url) for s in page.items],
            "grid_note": GRID_NOTE,
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_site(site_id: str) -> dict[str, Any]:
        """One site's card: score breakdown, both profile verdicts with blockers, and every fact with its
        source, date and confidence (verified, estimated or missing)."""
        detail = await run_db(lambda session, user: sites.get_site(site_id, session, user))
        out = detail.model_dump(mode="json", exclude=SITE_DETAIL_DROPPED)
        return {**out, "url": site_url(public_url, site_id), "grid_note": GRID_NOTE}

    @mcp.tool(annotations=READ_ONLY)
    async def list_pipeline() -> dict[str, Any]:
        """Sites the team put in the deal pipeline, with their stage. Notes are never shared."""

        def query(session: Any, _user: Any) -> list[dict[str, Any]]:
            rows = session.execute(
                text(
                    "SELECT c.site_id, c.stage, c.updated_at, s.area_ha, s.estimated_mw, s.score, "
                    "pw.name AS powiat FROM project_cards c "
                    "JOIN sites s ON s.site_id = c.site_id "
                    "LEFT JOIN admin_units pw ON pw.level = 'powiat' AND pw.teryt = s.powiat "
                    "ORDER BY c.updated_at DESC"
                )
            ).mappings()
            return [dict(r) for r in rows]

        rows = await run_db(query)
        items = [
            {
                "site_id": r["site_id"],
                "url": site_url(public_url, r["site_id"]),
                "stage": r["stage"],
                "updated_at": r["updated_at"].isoformat(),
                "powiat": r["powiat"],
                "area_ha": round(r["area_ha"], 2),
                "estimated_mw": round(r["estimated_mw"], 1),
                "score": round(r["score"], 1),
            }
            for r in rows
        ]
        return {"count": len(items), "items": items}

    @mcp.tool(annotations=READ_ONLY)
    async def list_powiaty(
        voivodeship: VoivodeshipCode = None,
        checked: Annotated[
            bool | None,
            Field(description="true: searched powiats (default); false: not yet searched; null: all"),
        ] = True,
    ) -> dict[str, Any]:
        """Which powiats were searched, when, and how many sites each gave; also the next in the queue."""
        listing = await run_db(lambda session, _user: powiats.list_powiats(session))
        chosen = [
            p
            for p in listing.items
            if (checked is None or p.checked == checked)
            and (voivodeship is None or p.voivodeship_teryt == voivodeship)
        ]
        return {
            "searched": sum(p.checked for p in listing.items),
            "all_powiats": len(listing.items),
            "next_up": listing.next_up,
            "powiaty": [
                {
                    "teryt": p.teryt,
                    "name": p.name,
                    "voivodeship": p.voivodeship_name,
                    "searched": p.checked,
                    "last_ok_at": p.last_ok_at.isoformat() if p.last_ok_at else None,
                    "sites": p.sites,
                    "priority": p.priority,
                    "in_aoi": p.in_aoi,
                }
                for p in chosen
            ],
        }

    @mcp.tool(annotations=READ_ONLY)
    async def coverage(
        level: Literal["wojewodztwo", "powiat"] = "wojewodztwo",
        item: Annotated[
            Literal["all", "zoning", "pen_soil_iii", "bonus_psi", "grid_headroom"],
            Field(description="Which data item the share of sites with data refers to"),
        ] = "all",
    ) -> dict[str, Any]:
        """How complete the data is: per area, the number of kept sites and the share that have data."""
        result = await run_db(
            lambda session, _user: coverage_router.get_coverage(
                session=session, level=level, item=item, include_geometry=False
            )
        )
        return {
            "level": result.level,
            "item": result.item,
            "areas": [
                {
                    "teryt": a.teryt,
                    "name": a.name,
                    "sites": a.sites,
                    "with_data": a.with_data,
                    "total": a.total,
                    "share": round(a.share, 2),
                }
                for a in result.areas
                if a.sites > 0
            ],
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_check(check_id: str) -> dict[str, Any]:
        """The result of a parcel check ("Sprawdź działkę"): status, steps and the verdict per parcel."""
        check = await run_db(lambda session, user: checks.get_check(check_id, session, user))
        out = check.model_dump(mode="json", exclude={"requested_by"})
        return {**out, "url": f"{public_url.rstrip('/')}/sprawdz-dzialke/{check_id}", "grid_note": GRID_NOTE}

    @mcp.tool(annotations=READ_ONLY)
    async def scoring_rules() -> dict[str, Any]:
        """The scoring weights and thresholds: use it to explain how a score is built."""
        cfg = await run_db(lambda _session, _user: config.scoring_config())
        return cfg.model_dump(mode="json")
