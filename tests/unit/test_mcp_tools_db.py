"""MCP read tools against a real PostGIS database: the data they return, and what must never leave."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.mcp import context
from dclh_api.mcp.server import build_mcp
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import score_candidates, write_sites
from tests.unit.test_mcp_tools import call
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

NOTE = "TAJNA-NOTATKA-123"
READ_TOOLS = ("search_sites", "list_pipeline", "list_powiaty", "coverage", "scoring_rules")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def seeded() -> Iterator[None]:
    with session_scope() as session:
        write_sites(
            session,
            score_candidates(parcel_sites(), layers(), CFG),
            scored_at=datetime(2026, 10, 10, 6, 0, tzinfo=UTC),
            powiat="1421",
        )
    yield
    with session_scope() as session:
        session.execute(text("DELETE FROM project_cards"))
        session.execute(text("DELETE FROM sites"))


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    with session_scope() as session:
        user_id = session.execute(
            text(
                "INSERT INTO users (email, password_hash, role) VALUES ('ann@example.org', 'x', 'member') "
                "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
            )
        ).scalar_one()

    @contextmanager
    def real_session() -> Iterator[Session]:
        with session_scope() as session:
            yield session

    monkeypatch.setattr(context, "tool_session", real_session)
    monkeypatch.setattr(
        context, "tool_user", lambda _s: UserRow(id=user_id, email="ann@example.org", role="member")
    )
    return build_mcp("https://dclh.example")


async def first_site_id(mcp: FastMCP) -> str:
    found = await call(mcp, "search_sites", min_mw=0)
    return str(found["sites"][0]["site_id"])


async def put_in_pipeline(site_id: str, stage: str = "analysis") -> None:
    with session_scope() as session:
        session.execute(
            text("INSERT INTO project_cards (site_id, stage, owner_note) VALUES (:s, :stage, :note)"),
            {"s": site_id, "stage": stage, "note": NOTE},
        )


async def test_search_returns_the_seeded_sites_best_first(seeded: None, mcp: FastMCP) -> None:
    result = await call(mcp, "search_sites", min_mw=0)
    scores = [s["score"] for s in result["sites"]]
    assert result["total"] == len(scores) == 2 and scores == sorted(scores, reverse=True)


async def test_list_pipeline_returns_the_stage_and_never_the_note(seeded: None, mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    await put_in_pipeline(site_id, "negotiation")
    result = await call(mcp, "list_pipeline")
    assert result["count"] == 1
    assert result["items"][0]["site_id"] == site_id and result["items"][0]["stage"] == "negotiation"
    assert NOTE not in json.dumps(result)
    assert "updated_by" not in result["items"][0] and "owner_note" not in result["items"][0]


async def test_owner_note_never_leaks_through_any_read_tool(seeded: None, mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    await put_in_pipeline(site_id)
    arguments: dict[str, dict[str, Any]] = {name: {} for name in READ_TOOLS}
    arguments["search_sites"] = {"min_mw": 0}
    arguments["get_site"] = {"site_id": site_id}
    for name, args in arguments.items():
        assert NOTE not in json.dumps(await call(mcp, name, **args)), name


async def test_get_site_has_the_stage_but_no_geometry_map_or_note(seeded: None, mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    await put_in_pipeline(site_id, "watching")
    card = await call(mcp, "get_site", site_id=site_id)
    assert card["stage"] == "watching" and card["features"] and card["items"]
    assert {"geometry", "map", "project_note"}.isdisjoint(card)
    assert all({"source_id", "as_of", "confidence"} <= set(f) for f in card["features"].values())


async def test_unknown_site_is_a_tool_error(seeded: None, mcp: FastMCP) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match=r"Nie ma takiej lokalizacji\."):
        await call(mcp, "get_site", site_id="nope")


async def test_a_malformed_check_id_is_a_tool_error(seeded: None, mcp: FastMCP) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match=r"Nie ma takiego sprawdzenia\."):
        await call(mcp, "get_check", check_id="not-a-uuid")
