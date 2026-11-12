"""MCP `search_now` against a real database: roles, scopes, AOI, duplicates and the daily cap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.mcp import context, tools_search_now
from dclh_api.mcp.server import build_mcp
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from tests.unit.test_mcp_queue_db import UNITS, box, token
from tests.unit.test_mcp_tools import PUBLIC_URL, call

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

ADMIN = "admin@example.org"
MEMBER = "member@example.org"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def tables() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            for table in ("jobs", "admin_units", "aoi"):
                session.execute(text(f"DELETE FROM {table}"))

    wipe()
    with session_scope() as session:
        for teryt, level, name, parent, geom in UNITS:
            session.execute(
                text(
                    "INSERT INTO admin_units (teryt, level, name, parent_teryt, geom) "
                    f"VALUES (:t, :l, :n, :p, {geom})"
                ),
                {"t": teryt, "l": level, "n": name, "p": parent},
            )
        session.execute(text(f"INSERT INTO aoi (geom) VALUES ({box(0, 100)})"))
    yield
    wipe()


def make_user(email: str, role: str) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) VALUES (:e, 'x', :r) "
                    "ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role RETURNING id"
                ),
                {"e": email, "r": role},
            ).scalar_one()
        )


def server(monkeypatch: pytest.MonkeyPatch, email: str, role: str) -> tuple[FastMCP, int]:
    user_id = make_user(email, role)

    @contextmanager
    def real_session() -> Iterator[Session]:
        with session_scope() as session:
            yield session

    monkeypatch.setattr(context, "tool_session", real_session)
    monkeypatch.setattr(context, "tool_user", lambda _s: UserRow(id=user_id, email=email, role=role))
    return build_mcp(PUBLIC_URL), user_id


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastMCP]:
    built, user_id = server(monkeypatch, ADMIN, "admin")
    with token(["dclh.read", "dclh.write"], user_id):
        yield built


def jobs() -> list[tuple[str, str, dict[str, str], str | None]]:
    with session_scope() as session:
        rows = session.execute(text("SELECT kind, status, payload, requested_by FROM jobs ORDER BY id"))
        return [(r[0], r[1], r[2], r[3]) for r in rows]


def add_job(status: str, teryt: str, requested_by: str, days_ago: int = 0) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                text(
                    "INSERT INTO jobs (kind, status, payload, requested_by, created_at) "
                    "VALUES ('search', :s, CAST(:p AS jsonb), :r, now() - make_interval(days => :d)) RETURNING id"
                ),
                {"s": status, "p": f'{{"powiat": "{teryt}"}}', "r": requested_by, "d": days_ago},
            ).scalar_one()
        )


async def test_admin_queues_by_name_and_by_code_like_the_planner(admin: FastMCP) -> None:
    first = await call(admin, "search_now", powiat="wrocławski")
    assert first["powiat"] == "powiat wrocławski" and first["teryt"] == "0223"
    assert first["status"] == "pending" and first["requested_by"] == ADMIN
    assert first["searches_left_today"] == tools_search_now.MANUAL_SEARCHES_PER_DAY - 1
    assert "get_queue" in first["note"]
    second = await call(admin, "search_now", powiat="0201")
    assert second["teryt"] == "0201" and second["job_id"] != first["job_id"]
    assert jobs() == [
        ("search", "pending", {"powiat": "0223"}, ADMIN),
        ("search", "pending", {"powiat": "0201"}, ADMIN),
    ]
    queue = await call(admin, "get_queue")
    assert {j["job_id"] for j in queue["in_progress"]} == {first["job_id"], second["job_id"]}


async def test_a_member_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    built, user_id = server(monkeypatch, MEMBER, "member")
    with token(["dclh.read", "dclh.write"], user_id), pytest.raises(ToolError, match="administrator"):
        await call(built, "search_now", powiat="0223")
    assert jobs() == []


async def test_a_read_only_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    built, user_id = server(monkeypatch, ADMIN, "admin")
    with token(["dclh.read"], user_id), pytest.raises(ToolError, match=r"dclh\.write"):
        await call(built, "search_now", powiat="0223")
    assert jobs() == []


@pytest.mark.parametrize(("value", "message"), [("lubelski", "poza obszarem"), ("nie ma takiego", ".")])
async def test_outside_the_aoi_and_unknown_are_refused(admin: FastMCP, value: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        await call(admin, "search_now", powiat=value)
    assert jobs() == []


@pytest.mark.parametrize("status", ["pending", "running"])
async def test_a_powiat_already_searching_is_refused_naming_the_job(admin: FastMCP, status: str) -> None:
    job_id = add_job(status, "0223", "planner")
    with pytest.raises(ToolError, match=rf"zadanie {job_id}"):
        await call(admin, "search_now", powiat="wrocławski")
    assert len(jobs()) == 1
    assert (await call(admin, "search_now", powiat="0201"))["teryt"] == "0201"


async def test_a_finished_search_does_not_block(admin: FastMCP) -> None:
    add_job("done", "0223", "planner")
    assert (await call(admin, "search_now", powiat="0223"))["status"] == "pending"


async def test_the_daily_cap_counts_manual_searches_only_and_resets(admin: FastMCP) -> None:
    add_job("done", "0218", "planner")  # the scheduler's jobs do not count
    add_job("failed", "0218", "scheduler")
    add_job("done", "0218", MEMBER, days_ago=1)  # yesterday's does not count
    make_user(MEMBER, "member")  # only emails of real users count as manual
    cap = tools_search_now.MANUAL_SEARCHES_PER_DAY
    left = []
    for teryt in ("0223", "0264", "0201")[:cap]:
        left.append((await call(admin, "search_now", powiat=teryt))["searches_left_today"])
    assert left == list(range(cap - 1, -1, -1))
    with pytest.raises(ToolError, match="limit"):
        await call(admin, "search_now", powiat="0218")
    assert len(jobs()) == 3 + cap
    with session_scope() as session:  # the next UTC day: move today's jobs back a day
        session.execute(text("UPDATE jobs SET created_at = created_at - interval '1 day'"))
    assert (await call(admin, "search_now", powiat="0218"))["searches_left_today"] == cap - 1


async def test_annotations() -> None:
    tools = {t.name: t for t in await build_mcp(PUBLIC_URL).list_tools()}
    ann = tools["search_now"].annotations
    assert ann is not None
    assert (ann.readOnlyHint, ann.destructiveHint, ann.idempotentHint, ann.openWorldHint) == (
        False,
        False,
        False,
        True,
    )
    assert "Administrators only" in (tools["search_now"].description or "")
