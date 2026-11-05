"""MCP write tools against a real database: real watch rows, a real queued check, and the existing limits."""

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
from dclh_api.routers import checks
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import score_candidates, write_sites
from tests.unit.test_mcp_tools import PUBLIC_URL, _TokenWithUser, call
from tests.unit.test_mcp_tools_db import NOTE, first_site_id, put_in_pipeline
from tests.unit.test_mcp_write import parcel, resolved
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

ID = "142104_2.0013.562/2"
EMAIL = "ann@example.org"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            for table in ("parcel_checks", "jobs", "project_cards", "watches", "sites"):
                session.execute(text(f"DELETE FROM {table}"))

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


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastMCP]:
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
    # The ULDK lookup is a network call: stub it, but let the real start_check queue the real check.
    monkeypatch.setattr(checks, "resolve", lambda body, _session: resolved(parcel(body.text.strip())))
    reset = auth_context_var.set(
        AuthenticatedUser(
            _TokenWithUser(token="t", client_id="c", scopes=["dclh.read", "dclh.write"], user_id=user_id)
        )
    )
    yield build_mcp(PUBLIC_URL)
    auth_context_var.reset(reset)


def watch_rows() -> int:
    with session_scope() as session:
        return int(session.execute(text("SELECT count(*) FROM watches")).scalar_one())


async def test_watching_twice_leaves_one_row_and_the_card_shows_it(mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    assert (await call(mcp, "watch_site", site_id=site_id))["watched"] is True
    assert (await call(mcp, "watch_site", site_id=site_id))["watched"] is True
    assert watch_rows() == 1
    assert (await call(mcp, "get_site", site_id=site_id))["watched"] is True


async def test_unwatching_removes_the_row_and_is_safe_to_repeat(mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    await call(mcp, "watch_site", site_id=site_id)
    assert (await call(mcp, "unwatch_site", site_id=site_id))["watched"] is False
    assert (await call(mcp, "unwatch_site", site_id=site_id))["watched"] is False
    assert watch_rows() == 0


async def test_an_unknown_site_cannot_be_watched(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match=r"Nie ma takiej lokalizacji\."):
        await call(mcp, "watch_site", site_id="nope")
    assert watch_rows() == 0


async def test_check_parcel_queues_a_check_that_get_check_can_read(mcp: FastMCP) -> None:
    queued = await call(mcp, "check_parcel", parcel_id=ID)
    assert queued["status"] == "pending" and queued["parcel"]["parcel_id"] == ID
    read = await call(mcp, "get_check", check_id=queued["check_id"])
    assert read["id"] == queued["check_id"] and read["status"] == "pending" and read["parcels"] == [ID]
    assert "requested_by" not in read
    with session_scope() as session:
        row = session.execute(
            text("SELECT requested_by, job_id FROM parcel_checks WHERE id = CAST(:i AS uuid)"),
            {"i": queued["check_id"]},
        ).one()
        assert row.requested_by == EMAIL and row.job_id is not None
        assert session.execute(
            text("SELECT kind FROM jobs WHERE id = :j"), {"j": row.job_id}
        ).scalar_one() == ("check_parcel")


async def test_a_malformed_parcel_id_gives_the_checks_own_polish_message(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Were the lookup to hand back something that is not an id, the real start_check still refuses it.
    monkeypatch.setattr(checks, "resolve", lambda _body, _session: resolved(parcel("not an id")))
    with pytest.raises(ToolError, match="To nie są identyfikatory działek"):
        await call(mcp, "check_parcel", parcel_id="not an id")
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM parcel_checks")).scalar_one() == 0


async def test_the_existing_limit_of_running_checks_applies(mcp: FastMCP) -> None:
    for n in range(checks.MAX_RUNNING_PER_USER):
        await call(mcp, "check_parcel", parcel_id=f"142104_2.0013.{n + 1}")
    with pytest.raises(ToolError, match="Masz już 3 sprawdzenia w toku"):
        await call(mcp, "check_parcel", parcel_id="142104_2.0013.99")
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM parcel_checks")).scalar_one() == 3


async def test_the_owner_note_never_leaks_through_a_write_tool(mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    await put_in_pipeline(site_id)
    results = [
        await call(mcp, "watch_site", site_id=site_id),
        await call(mcp, "unwatch_site", site_id=site_id),
        await call(mcp, "check_parcel", parcel_id=ID),
    ]
    assert NOTE not in json.dumps(results)
