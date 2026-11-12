"""MCP deal tools against a real database: set_stage keeps the owner note, remove_from_pipeline deletes one card."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.mcp import context
from dclh_api.mcp.server import build_mcp
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import score_candidates, write_sites
from tests.unit.test_mcp_tools import PUBLIC_URL, _TokenWithUser, call
from tests.unit.test_mcp_tools_db import NOTE, put_in_pipeline
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

EMAIL = "deals@example.org"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def wipe() -> None:
    with session_scope() as session:
        for table in ("project_cards", "sites"):
            session.execute(text(f"DELETE FROM {table}"))


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    wipe()
    with session_scope() as session:
        write_sites(
            session,
            score_candidates(parcel_sites(), layers(), CFG),
            scored_at=datetime(2026, 10, 10, 6, 0, tzinfo=UTC),
            powiat="1421",
        )
    yield
    wipe()


def build(monkeypatch: pytest.MonkeyPatch, scopes: list[str]) -> Iterator[FastMCP]:
    with session_scope() as session:
        user_id = session.execute(
            text(
                "INSERT INTO users (email, password_hash, role) VALUES (:e, 'x', 'member') "
                "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
            ),
            {"e": EMAIL},
        ).scalar_one()

    @contextmanager
    def real_session() -> Iterator[Session]:
        with session_scope() as session:
            yield session

    monkeypatch.setattr(context, "tool_session", real_session)
    monkeypatch.setattr(context, "tool_user", lambda _s: UserRow(id=user_id, email=EMAIL, role="member"))
    reset = auth_context_var.set(
        AuthenticatedUser(_TokenWithUser(token="t", client_id="c", scopes=scopes, user_id=user_id))
    )
    yield build_mcp(PUBLIC_URL)
    auth_context_var.reset(reset)


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastMCP]:
    yield from build(monkeypatch, ["dclh.read", "dclh.write"])


@pytest.fixture
def read_only(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastMCP]:
    yield from build(monkeypatch, ["dclh.read"])


def site_ids() -> list[str]:
    with session_scope() as session:
        return [r[0] for r in session.execute(text("SELECT site_id FROM sites ORDER BY site_id"))]


def card(site_id: str) -> tuple[str, str | None] | None:
    with session_scope() as session:
        row = session.execute(
            text("SELECT stage, owner_note FROM project_cards WHERE site_id = :s"), {"s": site_id}
        ).one_or_none()
        return None if row is None else (row[0], row[1])


async def test_set_stage_creates_the_card_and_is_idempotent(mcp: FastMCP) -> None:
    site_id = site_ids()[0]
    for _ in range(2):
        result = await call(mcp, "set_stage", site_id=site_id, stage="negotiation")
        assert result == {
            "site_id": site_id,
            "stage": "negotiation",
            "url": f"{PUBLIC_URL}/lokalizacje/{site_id}",
        }
    assert card(site_id) == ("negotiation", None)
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM project_cards")).scalar_one() == 1


@pytest.mark.parametrize(
    "stage", ["new", "watching", "analysis", "owner_contact", "negotiation", "rejected", "secured"]
)
async def test_every_app_stage_is_accepted(mcp: FastMCP, stage: str) -> None:
    site_id = site_ids()[0]
    assert (await call(mcp, "set_stage", site_id=site_id, stage=stage))["stage"] == stage


async def test_unknown_stage_lists_the_valid_ones_and_saves_nothing(mcp: FastMCP) -> None:
    site_id = site_ids()[0]
    with pytest.raises(ToolError, match=r"Nieznany etap „deal”\. Dostępne etapy: new, watching, analysis"):
        await call(mcp, "set_stage", site_id=site_id, stage="deal")
    assert card(site_id) is None


async def test_unknown_site_is_a_polish_error_and_creates_no_card(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match=r"Nie ma takiej lokalizacji\."):
        await call(mcp, "set_stage", site_id="nope", stage="analysis")
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM project_cards")).scalar_one() == 0


async def test_set_stage_keeps_the_owner_note_and_never_returns_it(mcp: FastMCP) -> None:
    site_id = site_ids()[0]
    await put_in_pipeline(site_id, "analysis")
    result = await call(mcp, "set_stage", site_id=site_id, stage="secured")
    assert card(site_id) == ("secured", NOTE)
    assert NOTE not in json.dumps(result)
    listed = await call(mcp, "list_pipeline")
    assert listed["items"][0]["stage"] == "secured" and NOTE not in json.dumps(listed)


async def test_remove_deletes_only_that_card_and_is_idempotent(mcp: FastMCP) -> None:
    first, second = site_ids()[:2]
    await put_in_pipeline(first)
    await put_in_pipeline(second, "watching")
    for _ in range(2):
        result = await call(mcp, "remove_from_pipeline", site_id=first)
        assert result["in_pipeline"] is False and NOTE not in json.dumps(result)
    assert card(first) is None and card(second) == ("watching", NOTE)


async def test_remove_for_a_site_without_a_card_is_fine(mcp: FastMCP) -> None:
    assert (await call(mcp, "remove_from_pipeline", site_id=site_ids()[0]))["in_pipeline"] is False


async def test_remove_is_marked_destructive_and_says_the_note_goes(mcp: FastMCP) -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    remove, stage = tools["remove_from_pipeline"], tools["set_stage"]
    assert remove.annotations is not None and remove.annotations.destructiveHint is True
    assert stage.annotations is not None and stage.annotations.destructiveHint is False
    assert "note" in (remove.description or "") and "confirm" in (remove.description or "")


async def test_a_read_only_token_cannot_change_the_pipeline(read_only: FastMCP) -> None:
    site_id = site_ids()[0]
    await put_in_pipeline(site_id)
    with pytest.raises(ToolError, match=r"dclh\.write"):
        await call(read_only, "set_stage", site_id=site_id, stage="rejected")
    with pytest.raises(ToolError, match=r"dclh\.write"):
        await call(read_only, "remove_from_pipeline", site_id=site_id)
    assert card(site_id) == ("analysis", NOTE)
