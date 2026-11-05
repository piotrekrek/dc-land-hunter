"""MCP write tools without a database: the scope check, the routers called, and what reaches Claude."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from dclh_api.mcp import context
from dclh_api.mcp.server import build_mcp
from dclh_api.routers import checks, sites
from dclh_api.schemas import WatchState
from dclh_pipeline.db.models import UserRow
from tests.unit.test_mcp_tools import PUBLIC_URL, _TokenWithUser, call

pytestmark = pytest.mark.anyio

CHECK_ID = "5b0c7a52-0a54-4f0a-9d11-6a1d1f1c9d00"
NO_WRITE = r"Brak uprawnienia do zapisu \(dclh\.write\)\."
CALLS: dict[str, dict[str, Any]] = {
    "watch_site": {"site_id": "site-1"},
    "unwatch_site": {"site_id": "site-1"},
    "check_parcel": {"parcel_id": "142104_2.0013.562/2"},
}


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


@contextmanager
def token_with(scopes: list[str]) -> Iterator[None]:
    reset = auth_context_var.set(
        AuthenticatedUser(_TokenWithUser(token="t", client_id="c", scopes=scopes, user_id=7))
    )
    try:
        yield
    finally:
        auth_context_var.reset(reset)


@pytest.fixture
def writer() -> Iterator[None]:
    with token_with(["dclh.read", "dclh.write"]):
        yield


def parcel(parcel_id: str = "142104_2.0013.562/2") -> checks.ResolvedParcelOut:
    return checks.ResolvedParcelOut(
        parcel_id=parcel_id,
        powiat_teryt="1421",
        voivodeship="wielkopolskie",
        county="koniński",
        commune="Konin",
        obreb="0013",
        area_ha=12.3456,
        lat=52.2,
        lon=18.2,
        in_aoi=True,
        source_text=parcel_id,
        outline={"type": "Polygon", "coordinates": []},
    )


def resolved(
    *items: checks.ResolvedParcelOut, issues: list[checks.IssueOut] | None = None
) -> checks.ResolveOut:
    return checks.ResolveOut(items=list(items), issues=issues or [], dropped=0, duplicates=0)


def forbid_routers(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("the router must not be called")

    for module, names in ((sites, ("watch_site", "unwatch_site")), (checks, ("resolve", "start_check"))):
        for name in names:
            monkeypatch.setattr(module, name, boom)


async def test_the_write_tools_say_honestly_what_they_do(mcp: FastMCP) -> None:
    tools = {t.name: t.annotations for t in await mcp.list_tools() if t.name in CALLS}
    assert set(tools) == set(CALLS)
    for name, hints in tools.items():
        assert hints is not None and hints.readOnlyHint is False and hints.destructiveHint is False, name
    assert tools["watch_site"].idempotentHint is True and tools["unwatch_site"].idempotentHint is True  # type: ignore[union-attr]
    assert tools["check_parcel"].idempotentHint is False  # type: ignore[union-attr]


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_a_read_only_token_cannot_write(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    forbid_routers(monkeypatch)
    with token_with(["dclh.read"]), pytest.raises(ToolError, match=NO_WRITE):
        await call(mcp, name, **CALLS[name])


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_a_call_without_any_token_cannot_write(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    forbid_routers(monkeypatch)
    with pytest.raises(ToolError, match=NO_WRITE):
        await call(mcp, name, **CALLS[name])


@pytest.mark.usefixtures("writer")
async def test_watch_and_unwatch_act_for_the_token_user(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str, int | None]] = []

    def watch(site_id: str, _session: object, user: UserRow) -> WatchState:
        seen.append(("watch", site_id, user.id))
        return WatchState(watched=True)

    def unwatch(site_id: str, _session: object, user: UserRow) -> WatchState:
        seen.append(("unwatch", site_id, user.id))
        return WatchState(watched=False)

    monkeypatch.setattr(sites, "watch_site", watch)
    monkeypatch.setattr(sites, "unwatch_site", unwatch)
    on = await call(mcp, "watch_site", site_id="site-1")
    off = await call(mcp, "unwatch_site", site_id="site-1")
    assert on == {"site_id": "site-1", "watched": True, "url": f"{PUBLIC_URL}/lokalizacje/site-1"}
    assert off["watched"] is False
    assert seen == [("watch", "site-1", 7), ("unwatch", "site-1", 7)]


@pytest.mark.usefixtures("writer")
async def test_a_router_refusal_reaches_claude_in_polish(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*_a: Any, **_kw: Any) -> None:
        raise HTTPException(404, "Nie ma takiej lokalizacji.")

    monkeypatch.setattr(sites, "watch_site", missing)
    with pytest.raises(ToolError, match=r"Nie ma takiej lokalizacji\."):
        await call(mcp, "watch_site", site_id="nope")


@pytest.mark.usefixtures("writer")
async def test_check_parcel_resolves_then_queues_and_returns_a_small_answer(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[checks.CheckIn] = []

    def start(body: checks.CheckIn, _session: object, user: UserRow) -> checks.CreatedOut:
        queued.append(body)
        assert user.id == 7
        return checks.CreatedOut(id=CHECK_ID)

    monkeypatch.setattr(checks, "resolve", lambda _body, _session: resolved(parcel()))
    monkeypatch.setattr(checks, "start_check", start)
    result = await call(mcp, "check_parcel", parcel_id="142104_2.0013.562/2")
    assert queued == [checks.CheckIn(parcels=["142104_2.0013.562/2"])]
    assert result["check_id"] == CHECK_ID and result["status"] == "pending"
    assert result["url"] == f"{PUBLIC_URL}/sprawdz-dzialke/{CHECK_ID}"
    assert result["parcel"]["commune"] == "Konin" and result["parcel"]["in_aoi"] is True
    assert "outline" not in result["parcel"]


@pytest.mark.usefixtures("writer")
async def test_a_parcel_that_cannot_be_found_is_refused_before_anything_is_queued(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    issue = checks.IssueOut(text="abc", message_pl="Nie rozpoznaję „abc”. Wklej identyfikator działki.")
    monkeypatch.setattr(checks, "resolve", lambda _b, _s: resolved(issues=[issue]))
    monkeypatch.setattr(checks, "start_check", lambda *_a, **_kw: pytest.fail("nothing may be queued"))
    with pytest.raises(ToolError, match="Nie rozpoznaję „abc”"):
        await call(mcp, "check_parcel", parcel_id="abc")


@pytest.mark.usefixtures("writer")
@pytest.mark.parametrize("items", [[], [parcel("142104_2.0013.1"), parcel("142104_2.0013.2")]])
async def test_exactly_one_parcel_is_required(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, items: list[checks.ResolvedParcelOut]
) -> None:
    monkeypatch.setattr(checks, "resolve", lambda _b, _s: resolved(*items))
    monkeypatch.setattr(checks, "start_check", lambda *_a, **_kw: pytest.fail("nothing may be queued"))
    with pytest.raises(ToolError, match="Podaj dokładnie jedną działkę"):
        await call(mcp, "check_parcel", parcel_id="142104_2.0013.1\n142104_2.0013.2")


@pytest.mark.usefixtures("writer")
@pytest.mark.parametrize(
    ("failing", "detail"),
    [
        ("resolve", "Serwer nie ma ustawionego adresu kontaktowego (DCLH_CONTACT_EMAIL)."),
        ("start_check", "Masz już 3 sprawdzenia w toku."),
    ],
)
async def test_the_checks_own_refusals_reach_claude(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, failing: str, detail: str
) -> None:
    def refuse(*_a: Any, **_kw: Any) -> None:
        raise HTTPException(429, detail)

    monkeypatch.setattr(checks, "resolve", lambda _b, _s: resolved(parcel()))
    monkeypatch.setattr(checks, failing, refuse)
    with pytest.raises(ToolError, match=detail.replace("(", r"\(").replace(")", r"\)")):
        await call(mcp, "check_parcel", parcel_id="142104_2.0013.562/2")


@pytest.mark.usefixtures("writer")
@pytest.mark.parametrize("bad", ["", "x" * 101])
async def test_the_parcel_id_length_is_bounded(mcp: FastMCP, bad: str) -> None:
    with pytest.raises(ToolError):
        await call(mcp, "check_parcel", parcel_id=bad)
