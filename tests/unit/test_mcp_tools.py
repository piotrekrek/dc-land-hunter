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

from dclh_api.mcp import areas, context, search
from dclh_api.mcp.server import build_mcp
from dclh_api.routers import sites
from dclh_api.schemas import FactOut, SiteDetail, SiteMapOut, SiteSummary
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
    "top_picks",
    "compare_sites",
    "get_queue",
    "sites_near",

    "what_changed",

    "connection_costs",
}
# See test_mcp_write.py and test_mcp_queue_db.py.
WRITE_TOOLS = {
    "watch_site",
    "unwatch_site",
    "check_parcel",
    "add_priority",
    "remove_priority",
    "set_stage",
    "remove_from_pipeline",
    "search_now",
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
        weakest_label_pl=None,
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


def stub_search(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replaces the SQL of `search_sites`; returns the keyword arguments the tool passed to it."""
    seen: dict[str, Any] = {}

    def fake(_session: Any, _public_url: str, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return result or {"total": 0, "next_cursor": None, "verdict_counts": {}, "sites": []}

    monkeypatch.setattr(search, "search_sites", fake)
    return seen


@pytest.mark.parametrize(("asked", "sent"), [(500, 50), (0, 1), (-3, 1), (20, 20)])
async def test_search_limit_is_clamped(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, asked: int, sent: int
) -> None:
    seen = stub_search(monkeypatch)
    await call(mcp, "search_sites", limit=asked)
    assert seen["limit"] == sent


async def test_search_defaults_keep_the_old_behaviour(mcp: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = stub_search(monkeypatch)
    await call(mcp, "search_sites")
    assert seen | {"limit": 20} == {
        "powiat": None,
        "voivodeship": None,
        "min_mw": 60,
        "min_ha": 0,
        "profile": None,
        "hide_red": False,
        "verdicts": None,
        "sort": "score",
        "limit": 20,
        "cursor": None,
        "max_per_gmina": None,
        "min_distance_km": None,
        "full": False,
    }


async def test_search_result_carries_the_grid_note_and_passes_the_filters_on(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = stub_search(monkeypatch, {"total": 1, "next_cursor": "abc", "verdict_counts": {}, "sites": []})
    result = await call(
        mcp,
        "search_sites",
        powiat="2208",
        profile="container",
        verdict=["green", "yellow"],
        max_per_gmina=1,
        detail="full",
    )
    assert result["grid_note"] == context.GRID_NOTE and result["next_cursor"] == "abc"
    assert seen["powiat"] == "2208" and seen["verdicts"] == ["green", "yellow"]
    assert seen["max_per_gmina"] == 1 and seen["full"] is True


async def test_area_names_are_resolved_to_codes_with_the_voivodeship_narrowing_the_powiat(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = stub_search(monkeypatch)
    asked: list[tuple[str, str | None, str | None]] = []

    def fake_resolve(_s: Any, level: str, value: str | None, *, within: str | None = None) -> str | None:
        asked.append((level, value, within))
        return {"dolnośląskie": "02", "średzki": "0218"}.get(value or "")

    monkeypatch.setattr(areas, "resolve_area", fake_resolve)
    await call(mcp, "search_sites", voivodeship="dolnośląskie", powiat="średzki")
    assert seen["voivodeship"] == "02" and seen["powiat"] == "0218"
    assert ("powiat", "średzki", "02") in asked


@pytest.mark.parametrize("arguments", [{"powiat": "22088"}, {"voivodeship": "2"}])
async def test_a_numeric_code_of_the_wrong_length_is_rejected(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch, arguments: dict[str, str]
) -> None:
    stub_search(monkeypatch)
    with pytest.raises(ToolError, match="cyfry"):
        await call(mcp, "search_sites", **arguments)


async def test_a_verdict_filter_needs_a_profile(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match="wymagają wybranego profilu"):
        search.search_sites(
            object(),  # type: ignore[arg-type]
            PUBLIC_URL,
            powiat=None,
            voivodeship=None,
            min_mw=60,
            min_ha=0,
            profile=None,
            hide_red=False,
            verdicts=["green"],
            sort="score",
            limit=5,
            cursor=None,
        )


async def test_top_picks_is_one_per_gmina_and_region_with_green_and_yellow_by_default(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = stub_search(monkeypatch)
    await call(mcp, "top_picks", profile="container")
    assert seen["max_per_gmina"] == 1 and seen["max_per_voivodeship"] == 1
    assert seen["min_distance_km"] == 10
    assert seen["verdicts"] == ["green", "yellow"] and seen["profile"] == "container"
    assert seen["sort"] == "score" and seen["limit"] == 10
    await call(mcp, "top_picks", profile="built", per_region=3, verdict=["manual"], limit=500)
    assert seen["max_per_voivodeship"] == 3 and seen["verdicts"] == ["manual"] and seen["limit"] == 50


def square(x: float, y: float, side: float = 100) -> Any:
    from shapely.geometry import box

    return box(x, y, x + side, y + side)


def test_spread_keeps_the_best_of_each_cluster_and_measures_from_outline_to_outline() -> None:
    ranked = [
        ("a", "02", square(0, 0)),
        ("b", "02", square(5_000, 0)),  # 4.9 km from a: hidden at 10 km
        ("c", "02", square(30_000, 0)),
        ("d", "02", square(9_000, 0)),  # 8.9 km from a: hidden
        ("e", "02", square(12_000, 0)),  # 11.9 km from a: kept
    ]
    assert search.spread(ranked, 10_000, None) == ["a", "c", "e"]
    assert search.spread(ranked, 4_000, None) == ["a", "b", "c", "e"]  # d is 3.9 km from b


def test_a_site_hidden_by_a_better_one_does_not_hide_others_and_a_full_region_hides_nothing() -> None:
    ranked = [
        ("a", "02", square(0, 0)),
        ("b", "02", square(8_000, 0)),  # hidden by a
        ("c", "04", square(16_000, 0)),  # 15.9 km from a, 8 km from b: kept, b hid nothing
        ("d", "02", square(40_000, 0)),  # region 02 is full after a
        ("e", "04", square(60_000, 0)),  # region 04 is full after c
    ]
    assert search.spread(ranked, 10_000, None) == ["a", "c", "d", "e"]
    assert search.spread(ranked, 10_000, 1) == ["a", "c"]
    assert search.spread([], 10_000, 1) == []


def test_cursor_round_trips_and_rejects_anything_else() -> None:
    assert search.decode_cursor(search.encode_cursor(40)) == 40
    for bad in ("", "abc", "!!!", search.encode_cursor(1)[:-2], "bzFh"):
        with pytest.raises(ToolError, match="Kursor"):
            search.decode_cursor(bad)


@pytest.mark.parametrize(
    ("typed", "normalized"),
    [
        ("Dolnośląskie", "dolnoslaskie"),
        ("powiat Wrocławski", "wroclawski"),
        ("  Woj. Łódzkie ", "lodzkie"),
        ("województwo kujawsko-pomorskie", "kujawsko-pomorskie"),
        ("ŁĘBORK", "lebork"),
    ],
)
def test_names_are_compared_without_case_diacritics_or_the_word_powiat(typed: str, normalized: str) -> None:
    assert areas.normalize(typed) == normalized


def test_codes_pass_through_and_nothing_means_no_filter() -> None:
    assert areas.resolve_area(None, "powiat", "2208") == "2208"  # type: ignore[arg-type]
    assert areas.resolve_area(None, "voivodeship", None) is None  # type: ignore[arg-type]
    assert areas.resolve_area(None, "voivodeship", "  ") is None  # type: ignore[arg-type]


async def test_get_site_needs_exactly_one_of_site_id_and_site_ids(mcp: FastMCP) -> None:
    for arguments in ({}, {"site_id": "a", "site_ids": ["b"]}):
        with pytest.raises(ToolError, match="site_id albo site_ids"):
            await call(mcp, "get_site", **arguments)


async def test_bulk_get_site_lists_unknown_ids_instead_of_failing(
    mcp: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = SiteDetail(
        **summary().model_dump(),
        parcel_ids=[],
        scored_at=datetime(2026, 11, 1, tzinfo=UTC),
        exclusions=[],
        categories=[],
        items=[],
        features={},
        geometry={},
        map=SiteMapOut(origin_2180=[0, 0], bbox_m=[0, 0, 1, 1], parcel=[]),
    )

    def fake(site_id: str, *_a: Any, **_kw: Any) -> SiteDetail:
        if site_id == "gone":
            raise HTTPException(404, "Nie ma takiej lokalizacji.")
        return detail

    monkeypatch.setattr(sites, "get_site", fake)
    result = await call(mcp, "get_site", site_ids=["site-1", "gone", "site-1"])
    assert [s["site_id"] for s in result["sites"]] == ["site-1"] and result["not_found"] == ["gone"]
    assert {"geometry", "map", "project_note"}.isdisjoint(result["sites"][0])


async def test_comparing_needs_two_to_eight_sites(mcp: FastMCP) -> None:
    for ids in (["a"], [f"s{i}" for i in range(9)]):
        with pytest.raises(ToolError):
            await call(mcp, "compare_sites", site_ids=ids)


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
