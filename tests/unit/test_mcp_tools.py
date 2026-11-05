"""MCP read tools without a database: the routers are stubbed, so these tests pin the tool layer itself."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from dclh_api.mcp import context
from dclh_api.mcp.server import build_mcp
from dclh_api.routers import sites
from dclh_api.schemas import FactOut, SiteDetail, SiteMapOut, SitesPage, SiteSummary
from dclh_pipeline.db.models import UserRow

pytestmark = pytest.mark.anyio

PUBLIC_URL = "https://dclh.example"
TOOLS = {
    "search_sites",
    "get_site",
    "list_pipeline",
    "list_powiaty",
    "coverage",
    "get_check",
    "scoring_rules",
}
WRITE_TOOLS = {"watch_site", "unwatch_site", "check_parcel"}  # see test_mcp_write.py


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    @contextmanager
    def no_session() -> Iterator[object]:
        yield object()

    monkeypatch.setattr(context, "tool_session", no_session)
    monkeypatch.setattr(
        context, "tool_user", lambda _s: UserRow(id=7, email="ann@example.org", role="member")
    )
    return build_mcp(PUBLIC_URL)


async def call(mcp: FastMCP, name: str, **arguments: Any) -> dict[str, Any]:
    result = await mcp.call_tool(name, arguments)
    # A tool typed `dict[str, Any]` answers with (text blocks, structured dict); both carry the same data.
    assert isinstance(result, tuple)
    blocks, structured = result
    assert json.loads(blocks[0].text) == structured
    assert isinstance(structured, dict)
    return structured


def summary(site_id: str = "site-1") -> SiteSummary:
    return SiteSummary(
        site_id=site_id,
        zone_id=None,
        area_ha=12.345,
        estimated_mw=74.07,
        score=71.83,
        upside_score=60,
        completeness=0.5,
        excluded=False,
        grid_badge="Bliskość sieci",
        large_campus=False,
        split_suggested=False,
        flags=[],
        lon=17.7,
        lat=54.5,
        gmina="Lębork",
        powiat="lęborski",
        voivodeship="pomorskie",
        mw_fact=FactOut(
            value=74.07, source_id="egib", as_of=datetime(2026, 11, 1, tzinfo=UTC), confidence="estimated"
        ),
        profile_categories={"built": "green", "container": "yellow"},
    )


async def test_the_read_tools_are_listed_and_all_read_only(mcp: FastMCP) -> None:
    listed = await mcp.list_tools()
    assert {t.name for t in listed} == TOOLS | WRITE_TOOLS
    for tool in listed:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is (tool.name in TOOLS), tool.name


@pytest.mark.parametrize(("asked", "sent"), [(500, 50), (0, 1), (-3, 1), (20, 20)])
async def test_search_limit_is_clamped(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, asked: int, sent: int
) -> None:
    seen: dict[str, Any] = {}

    def fake(**kwargs: Any) -> SitesPage:
        seen.update(kwargs)
        return SitesPage(total=0, offset=0, limit=kwargs["limit"], items=[])

    monkeypatch.setattr(sites, "list_sites", fake)
    await call(mcp, "search_sites", limit=asked)
    assert seen["limit"] == sent
    assert seen["include_excluded"] is False and seen["offset"] == 0


async def test_search_result_is_compact_and_carries_the_grid_note(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = SitesPage(total=1, offset=0, limit=20, items=[summary()], next_cursor="abc")
    monkeypatch.setattr(sites, "list_sites", lambda **_kw: page)
    result = await call(mcp, "search_sites", powiat="2208")
    assert result["grid_note"] == context.GRID_NOTE
    assert result["next_cursor"] == "abc" and result["total"] == 1
    [line] = result["sites"]
    assert line["url"] == f"{PUBLIC_URL}/lokalizacje/site-1"
    assert line["area_ha"] == 12.35 and line["estimated_mw"] == 74.1 and line["score"] == 71.8
    assert line["verdicts"] == {"built": "green", "container": "yellow"}
    assert {"lon", "lat", "mw_fact", "upside_score"}.isdisjoint(line)


@pytest.mark.parametrize("arguments", [{"powiat": "abc"}, {"powiat": "22088"}, {"voivodeship": "2"}])
async def test_malformed_teryt_codes_are_rejected(mcp: FastMCP, arguments: dict[str, str]) -> None:
    with pytest.raises(ToolError):
        await call(mcp, "search_sites", **arguments)


async def test_router_error_becomes_a_tool_error_with_the_polish_text(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*_a: Any, **_kw: Any) -> None:
        raise HTTPException(404, "Nie ma takiej lokalizacji.")

    monkeypatch.setattr(sites, "get_site", missing)
    with pytest.raises(ToolError, match=r"Nie ma takiej lokalizacji\."):
        await call(mcp, "get_site", site_id="nope")


class _TokenWithUser(AccessToken):
    user_id: int


async def test_the_token_user_reaches_the_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_session() -> Iterator[Any]:
        class Session:
            def get(self, _model: type, key: int) -> UserRow | None:
                return UserRow(id=key, email="ann@example.org", role="member")

        yield Session()

    monkeypatch.setattr(context, "tool_session", fake_session)
    reset = auth_context_var.set(
        AuthenticatedUser(_TokenWithUser(token="t", client_id="c", scopes=["dclh.read"], user_id=7))
    )
    try:
        assert await context.run_db(lambda _session, user: user.id) == 7
    finally:
        auth_context_var.reset(reset)


async def test_a_call_without_a_token_user_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_session() -> Iterator[object]:
        yield object()

    monkeypatch.setattr(context, "tool_session", fake_session)
    with pytest.raises(ToolError, match="Połącz Claude z aplikacją od nowa"):
        await context.run_db(lambda _session, user: user)


async def test_get_site_drops_the_geometry_the_map_and_the_project_note(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = SiteDetail(
        **summary().model_dump(),
        parcel_ids=["p1"],
        scored_at=datetime(2026, 11, 1, tzinfo=UTC),
        exclusions=[],
        categories=[],
        items=[],
        features={},
        geometry={"type": "Polygon", "coordinates": []},
        map=SiteMapOut(origin_2180=[0, 0], bbox_m=[0, 0, 1, 1], parcel=[]),
        stage="analysis",
        project_note="TAJNA-NOTATKA-123",
    )
    monkeypatch.setattr(sites, "get_site", lambda *_a, **_kw: detail)
    result = await call(mcp, "get_site", site_id="site-1")
    assert {"geometry", "map", "project_note"}.isdisjoint(result)
    assert result["stage"] == "analysis" and result["url"] == f"{PUBLIC_URL}/lokalizacje/site-1"
    assert result["grid_note"] == context.GRID_NOTE
    assert "TAJNA-NOTATKA-123" not in json.dumps(result)
