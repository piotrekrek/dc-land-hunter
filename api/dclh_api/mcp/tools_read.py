"""Read-only tools. Each calls the router function behind the matching screen, so Claude sees the UI's data.

Results stay small on purpose (Claude's context is scarce): lists are capped, and geometry, the map block
and the project note never leave the server. `project_cards.owner_note` may hold personal data (brief 9.6).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import text

from dclh_api.mcp import areas, search
from dclh_api.mcp.context import GRID_NOTE, run_db, site_url
from dclh_api.mcp.search import Verdict
from dclh_api.prices import compact, site_prices
from dclh_api.routers import checks, config, powiats, sites
from dclh_api.routers import coverage as coverage_router

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

MAX_SITES = 50  # the routers' own cap is 200; a page that big would fill Claude's context
SITE_DETAIL_DROPPED = {"geometry", "map", "project_note"}
MAX_CARDS = 10  # a full card is several KB
MAX_COMPARE = 8
HTTP_NOT_FOUND = 404

VoivodeshipCode = Annotated[str | None, Field(pattern=r"^\d{2}$", description="Voivodeship TERYT, 2 digits")]
AreaName = Annotated[
    str | None,
    Field(max_length=60, description="Name (any case, with or without Polish letters) or TERYT code"),
]
MinDistance = Annotated[
    float | None,
    Field(
        gt=0,
        le=200,
        description="Keep only sites at least this many km apart (outline to outline), best first: "
        "removes clusters that cross gmina borders",
    ),
]
Detail = Annotated[
    Literal["short", "full"],
    Field(description="short: top 3 things to check per site; full: everything, and all parcel ids"),
]


def _clamp(limit: int) -> int:
    return min(max(limit, 1), MAX_SITES)


def _areas(session: Any, powiat: str | None, voivodeship: str | None) -> tuple[str | None, str | None]:
    """Names become TERYT codes. The voivodeship narrows powiat names ("średzki" exists in two of them)."""
    voivodeship_code = areas.resolve_area(session, "voivodeship", voivodeship)
    return areas.resolve_area(session, "powiat", powiat, within=voivodeship_code), voivodeship_code


def register(mcp: FastMCP, public_url: str) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def search_sites(
        powiat: AreaName = None,
        voivodeship: AreaName = None,
        min_mw: Annotated[
            float,
            Field(
                ge=0, description="Minimum land capacity in MW (area x density, estimated_mw), not project_mw"
            ),
        ] = 60,
        min_ha: Annotated[float, Field(ge=0)] = 0,
        profile: Annotated[
            Literal["built", "container"] | None,
            Field(description="Investor profile that `hide_red`, `verdict` and the reasons refer to"),
        ] = None,
        hide_red: Annotated[
            bool, Field(description="Drop sites `profile` rejects (needs `profile`)")
        ] = False,
        verdict: Annotated[
            list[Verdict] | None,
            Field(description='Keep only these verdicts for `profile`, e.g. ["green", "yellow"]'),
        ] = None,
        max_per_gmina: Annotated[
            int | None,
            Field(
                ge=1, le=50, description="At most this many sites per gmina (best first): 1 avoids a cluster"
            ),
        ] = None,
        min_distance_km: MinDistance = None,
        sort: Literal["score", "upside", "area", "mw"] = "score",
        limit: Annotated[int, Field(description="Sites per page; capped at 50")] = 20,
        cursor: Annotated[str | None, Field(description="`next_cursor` of the previous page")] = None,
        detail: Detail = "short",
    ) -> dict[str, Any]:
        """Ranked candidate sites of 10 ha or more, best first, each with why (blockers and what is left to
        check, per profile), the nearest GPZ 110 kV (name, distance, confidence), parcel ids (for
        `check_parcel`) and how many facts are verified, estimated or missing. `verdict_counts` gives the
        sites per verdict under the other filters, so "how many are green" needs no paging. `powiat` and
        `voivodeship` take a name or a TERYT code. Use `cursor` for the next page."""

        def query(session: Any, _user: Any) -> dict[str, Any]:
            powiat_code, voivodeship_code = _areas(session, powiat, voivodeship)
            return search.search_sites(
                session,
                public_url,
                powiat=powiat_code,
                voivodeship=voivodeship_code,
                min_mw=min_mw,
                min_ha=min_ha,
                profile=profile,
                hide_red=hide_red,
                verdicts=verdict,
                sort=sort,
                limit=_clamp(limit),
                cursor=cursor,
                max_per_gmina=max_per_gmina,
                min_distance_km=min_distance_km,
                full=detail == "full",
            )

        return {**await run_db(query), "grid_note": GRID_NOTE}

    @mcp.tool(annotations=READ_ONLY)
    async def top_picks(
        profile: Annotated[Literal["built", "container"], Field(description="Investor profile")],
        per_region: Annotated[
            int, Field(ge=1, le=5, description="Best sites per voivodeship (one per gmina each)")
        ] = 1,
        verdict: Annotated[
            list[Verdict] | None, Field(description="Verdicts to keep; default green and yellow")
        ] = None,
        voivodeship: AreaName = None,
        min_distance_km: Annotated[
            float, Field(gt=0, le=200, description="Minimum km between picks; 10 keeps one per cluster")
        ] = 10,
        min_mw: Annotated[float, Field(ge=0)] = 60,
        limit: Annotated[int, Field(description="Sites in total; capped at 50")] = 10,
        detail: Detail = "short",
    ) -> dict[str, Any]:
        """The best sites from different places in one call: per voivodeship the top `per_region` by score,
        never two in one gmina and never two closer than `min_distance_km`. A thin layer on `search_sites`
        (same data, same reasons per site)."""

        def query(session: Any, _user: Any) -> dict[str, Any]:
            _, voivodeship_code = _areas(session, None, voivodeship)
            return search.search_sites(
                session,
                public_url,
                powiat=None,
                voivodeship=voivodeship_code,
                min_mw=min_mw,
                min_ha=0,
                profile=profile,
                hide_red=False,
                verdicts=verdict or ["green", "yellow"],
                sort="score",
                limit=_clamp(limit),
                cursor=None,
                max_per_gmina=1,
                max_per_voivodeship=per_region,
                min_distance_km=min_distance_km,
                full=detail == "full",
            )

        return {**await run_db(query), "grid_note": GRID_NOTE}

    @mcp.tool(annotations=READ_ONLY)
    async def get_site(
        site_id: Annotated[str | None, Field(description="One site id")] = None,
        site_ids: Annotated[
            list[str] | None, Field(max_length=MAX_CARDS, description=f"Up to {MAX_CARDS} site ids at once")
        ] = None,
    ) -> dict[str, Any]:
        """One site's card: score breakdown, both profile verdicts with blockers, and every fact with its
        source, date and confidence (verified, estimated or missing), plus `price_comparables` (RCN land
        transaction prices in zł/m² near the site, the asking price if set; informational). With `site_ids` it returns
        `{sites: [...cards], not_found: [...]}`; to compare sites side by side use `compare_sites`."""
        if (site_id is None) == (site_ids is None):
            raise ToolError("Podaj site_id albo site_ids (jedno z dwóch).")

        def card(session: Any, user: Any, one: str) -> dict[str, Any]:
            detail = sites.get_site(one, session, user)
            out = detail.model_dump(mode="json", exclude=SITE_DETAIL_DROPPED)
            prices = site_prices(session, one)  # RCN land-price comparables; informational, no score impact
            out["price_comparables"] = compact(prices) if prices else None
            return {**out, "url": site_url(public_url, one)}

        if site_id is not None:
            return {
                **await run_db(lambda session, user: card(session, user, site_id)),
                "grid_note": GRID_NOTE,
            }

        def many(session: Any, user: Any) -> dict[str, Any]:
            found: list[dict[str, Any]] = []
            missing: list[str] = []
            for one in dict.fromkeys(site_ids or []):
                try:
                    found.append(card(session, user, one))
                except HTTPException as exc:
                    if exc.status_code != HTTP_NOT_FOUND:
                        raise
                    missing.append(one)
            return {"sites": found, "not_found": missing}

        return {**await run_db(many), "grid_note": GRID_NOTE}

    @mcp.tool(annotations=READ_ONLY)
    async def compare_sites(
        site_ids: Annotated[
            list[str], Field(min_length=2, max_length=MAX_COMPARE, description="2 to 8 site ids")
        ],
        profile: Annotated[
            Literal["built", "container"] | None, Field(description="Show only this profile's reasons")
        ] = None,
    ) -> dict[str, Any]:
        """Sites side by side: area, estimated MW, score by category, verdicts, blockers and what is left to
        check, grid distances (with the nearest GPZ's name and the confidence of each) and data
        completeness."""

        def query(session: Any, _user: Any) -> dict[str, Any]:
            lines, missing = search.compare_lines(
                session, public_url, list(dict.fromkeys(site_ids)), profile=profile, full=True
            )
            return {"sites": lines, "not_found": missing}

        return {**await run_db(query), "grid_note": GRID_NOTE}

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
            Literal["all", "zoning", "pen_soil_iii", "bonus_psi", "grid_headroom", "pen_mineral_deposit"],
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
            "station_classes": [
                {
                    "teryt": c.teryt,
                    "name": c.name,
                    "operator_gpz": c.operator_gpz,
                    "generator": c.generator,
                    "customer": c.customer,
                    "unknown": c.unknown,
                    "unknown_share": round(c.unknown_share, 2),
                }
                for c in result.station_classes
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
