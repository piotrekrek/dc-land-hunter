"""MCP queue tools against a real database: read the Kolejka, add and remove priority powiats."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
from tests.unit.test_mcp_tools import PUBLIC_URL, _TokenWithUser, call

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

EMAIL = "ann@example.org"


def box(x0: int, x1: int) -> str:
    return f"ST_Multi(ST_GeomFromText('POLYGON(({x0} 0, {x1} 0, {x1} 10, {x0} 10, {x0} 0))', 2180))"


# AOI = x 0..100: the 02 powiats are inside, "0662" (east of the Vistula) is outside.
UNITS = (
    ("02", "voivodeship", "dolnośląskie", None, box(0, 100)),
    ("06", "voivodeship", "lubelskie", None, box(200, 300)),
    ("0223", "powiat", "powiat wrocławski", "02", box(10, 20)),
    ("0264", "powiat", "powiat Wrocław", "02", box(20, 30)),
    ("0218", "powiat", "powiat średzki", "02", box(30, 40)),
    ("0201", "powiat", "powiat bolesławiecki", "02", box(40, 50)),
    ("0662", "powiat", "powiat lubelski", "06", box(200, 210)),
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def tables() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            for table in ("jobs", "powiat_priority", "search_runs", "admin_units", "aoi"):
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


@contextmanager
def token(scopes: list[str], user_id: int) -> Iterator[None]:
    reset = auth_context_var.set(
        AuthenticatedUser(_TokenWithUser(token="t", client_id="c", scopes=scopes, user_id=user_id))
    )
    try:
        yield
    finally:
        auth_context_var.reset(reset)


def server(monkeypatch: pytest.MonkeyPatch) -> tuple[FastMCP, int]:
    with session_scope() as session:
        user_id = int(
            session.execute(
                text(
                    "INSERT INTO users (email, password_hash, role) VALUES (:e, 'x', 'member') "
                    "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id"
                ),
                {"e": EMAIL},
            ).scalar_one()
        )

    @contextmanager
    def real_session() -> Iterator[Session]:
        with session_scope() as session:
            yield session

    monkeypatch.setattr(context, "tool_session", real_session)
    monkeypatch.setattr(context, "tool_user", lambda _s: UserRow(id=user_id, email=EMAIL, role="member"))
    return build_mcp(PUBLIC_URL), user_id


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastMCP]:
    built, user_id = server(monkeypatch)
    with token(["dclh.read", "dclh.write"], user_id):
        yield built


def saved() -> list[tuple[str, str | None]]:
    with session_scope() as session:
        rows = session.execute(text("SELECT teryt, added_by FROM powiat_priority ORDER BY added_at, teryt"))
        return [(r[0], r[1]) for r in rows]


async def test_add_by_name_or_code_appends_in_order_and_records_who(mcp: FastMCP) -> None:
    first = await call(mcp, "add_priority", powiaty=["wrocławski"])
    assert first["added"] == ["0223"] and first["already_in_queue"] == []
    second = await call(mcp, "add_priority", powiaty=["0201", "Powiat Wrocławski", "Wrocław"])
    assert second["added"] == ["0201", "0264"] and second["already_in_queue"] == ["0223"]
    assert [t for t, _ in saved()] == ["0223", "0201", "0264"]  # the first one kept its place
    assert {who for _, who in saved()} == {EMAIL}
    assert [p["teryt"] for p in second["priority"]] == ["0223", "0201", "0264"]
    assert second["priority"][0] | {"added_at": None} == {
        "teryt": "0223",
        "name": "powiat wrocławski",
        "voivodeship": "dolnośląskie",
        "state": "waiting",
        "added_by": EMAIL,
        "added_at": None,
        "last_ok_at": None,
        "last_attempt_at": None,
        "last_status": None,
        "sites": None,
        "error": None,
    }


async def test_a_powiat_outside_the_aoi_is_refused_and_nothing_is_saved(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match="poza obszarem poszukiwań"):
        await call(mcp, "add_priority", powiaty=["wrocławski", "lubelski"])
    assert saved() == []


async def test_an_unknown_name_is_a_polish_error(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match="Nie znaleziono powiatu"):
        await call(mcp, "add_priority", powiaty=["Atlantyda"])
    with pytest.raises(ToolError, match="ma 4 cyfry"):
        await call(mcp, "remove_priority", powiaty=["02"])
    assert saved() == []


async def test_remove_keeps_the_rest_and_reports_what_was_not_there(mcp: FastMCP) -> None:
    await call(mcp, "add_priority", powiaty=["0223", "0201", "0264"])
    result = await call(mcp, "remove_priority", powiaty=["bolesławiecki", "średzki"])
    assert result["removed"] == ["0201"] and result["not_in_queue"] == ["0218"]
    assert [t for t, _ in saved()] == ["0223", "0264"]
    again = await call(mcp, "remove_priority", powiaty=["0201"])
    assert again["removed"] == [] and again["not_in_queue"] == ["0201"]


async def test_the_queue_shows_states_next_up_running_and_failing(mcp: FastMCP) -> None:
    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO search_runs (teryt, last_attempt_at, last_ok_at, status, sites) "
                "VALUES ('0201', now(), now(), 'ok', 3)"
            )
        )
        session.execute(
            text(
                "INSERT INTO jobs (kind, payload, status, started_at) VALUES "
                "('search', '{\"powiat\": \"0264\"}', 'running', now()), "
                "('search', '{\"powiat\": \"0218\"}', 'failed', NULL), "
                "('check', '{}', 'pending', NULL)"
            )
        )
        session.execute(text("UPDATE jobs SET finished_at = now(), error = 'boom' WHERE status = 'failed'"))
    await call(mcp, "add_priority", powiaty=["0223", "0201", "0264"])
    queue = await call(mcp, "get_queue")
    assert {p["teryt"]: p["state"] for p in queue["priority"]} == {
        "0223": "waiting",
        "0201": "searched",
        "0264": "in_progress",
    }
    searched = next(p for p in queue["priority"] if p["teryt"] == "0201")
    assert searched["sites"] == 3 and searched["last_status"] == "ok"
    # The waiting priority powiat goes first; the running one and the one outside the AOI are not planned.
    assert queue["next_up"][0] == {
        "teryt": "0223",
        "name": "powiat wrocławski",
        "voivodeship": "dolnośląskie",
        "priority": True,
    }
    assert {n["teryt"] for n in queue["next_up"]}.isdisjoint({"0264", "0662"})
    assert [(j["teryt"], j["status"]) for j in queue["in_progress"]] == [("0264", "running")]
    assert [(f["teryt"], f["error"]) for f in queue["failing"]] == [("0218", "boom")]
    assert "wspólna dla całego zespołu" in queue["note"]


async def test_a_read_only_token_can_read_the_queue_but_not_change_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built, user_id = server(monkeypatch)
    with token(["dclh.read"], user_id):
        assert (await call(built, "get_queue"))["priority"] == []
        for name in ("add_priority", "remove_priority"):
            with pytest.raises(ToolError, match=r"dclh\.write"):
                await call(built, name, powiaty=["0223"])
    assert saved() == []
