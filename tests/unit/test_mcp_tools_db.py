"""MCP read tools against a real PostGIS database: the data they return, and what must never leave."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
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
READ_TOOLS = (
    "search_sites",
    "list_pipeline",
    "list_powiaty",
    "coverage",
    "scoring_rules",
    "get_queue",
    "sites_near",
    "what_changed",
    "connection_costs",
)


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
    arguments["connection_costs"] = {"site_id": site_id}
    arguments["search_sites"] = {"min_mw": 0, "detail": "full"}
    arguments["sites_near"] = {"lat": 51.7, "lon": 19.4, "radius_km": 100, "min_mw": 0, "detail": "full"}
    arguments["top_picks"] = {"profile": "container", "verdict": ["green", "yellow", "red", "manual"]}
    arguments["compare_sites"] = {"site_ids": [site_id, "other"]}
    arguments["get_site"] = {"site_ids": [site_id]}
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


# --- search filters, grouping, names and bulk reads -------------------------------------------------------------

BOX = "ST_Multi(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 2180))"


@pytest.fixture
def places() -> Iterator[None]:
    """Admin units around the seeded sites (they sit near the test fixtures' X0, Y0)."""
    from tests.unit.test_scoring_run import X0, Y0

    def unit(teryt: str, level: str, name: str, parent: str | None, pad: float) -> None:
        box = BOX.format(x0=X0 - pad, y0=Y0 - pad, x1=X0 + 8_000 + pad, y1=Y0 + 2_000 + pad)
        session.execute(
            text(
                "INSERT INTO admin_units (teryt, level, name, parent_teryt, geom) "
                f"VALUES (:t, :l, :n, :p, {box})"
            ),
            {"t": teryt, "l": level, "n": name, "p": parent},
        )

    with session_scope() as session:
        unit("14", "voivodeship", "mazowieckie", None, 2_000)
        unit("1421", "powiat", "powiat lęborski", "14", 1_500)
        unit("1422", "powiat", "powiat Lębork", "14", 1_500)
        unit("0218", "powiat", "powiat średzki", "02", 1_500)
        unit("3025", "powiat", "powiat średzki", "30", 1_500)
        unit("142101", "gmina", "Gmina Testowa", "1421", 1_000)
    yield
    with session_scope() as session:
        session.execute(text("DELETE FROM admin_units"))


async def test_the_old_call_returns_what_the_web_list_returns(seeded: None, mcp: FastMCP) -> None:
    from dclh_api.routers import sites as sites_router

    with session_scope() as session:
        expected = sites_router.list_sites(
            session=session, sort="score", order="desc", min_mw=0, min_ha=0, include_excluded=False,
            powiat=None, voivodeship=None, profile=None, hide_red=False, limit=50, offset=0, cursor=None,
        )  # fmt: skip
    result = await call(mcp, "search_sites", min_mw=0)
    assert result["total"] == expected.total
    assert [s["site_id"] for s in result["sites"]] == [s.site_id for s in expected.items]


async def test_each_line_says_why_where_the_grid_is_and_how_complete_the_data_is(
    seeded: None, mcp: FastMCP
) -> None:
    result = await call(mcp, "search_sites", min_mw=0, profile="container")
    line = result["sites"][0]
    assert set(line["why"]) == {"container"} and {"blockers", "to_check", "to_check_total"} <= set(
        line["why"]["container"]
    )
    assert {"name", "distance_km", "confidence", "source_id"} <= set(line["nearest_gpz"])
    assert line["nearest_gpz"]["confidence"] in {"estimated", "verified", "missing"}
    assert line["parcels"]["count"] >= 1 and line["parcels"]["ids"] and line["parcels"]["obreb"]
    assert sum(line["data"].values()) > 0 and set(line["data"]) == {"verified", "estimated", "missing"}
    both = await call(mcp, "search_sites", min_mw=0)
    assert set(both["sites"][0]["why"]) == {"built", "container"}


async def test_verdict_filter_total_and_counts_agree(seeded: None, mcp: FastMCP) -> None:
    everything = await call(mcp, "search_sites", min_mw=0, profile="container")
    counts = everything["verdict_counts"]["container"]
    assert sum(counts.values()) == everything["total"] == 2
    kept = next(v for v, n in counts.items() if n)
    only = await call(mcp, "search_sites", min_mw=0, profile="container", verdict=[kept])
    assert only["total"] == counts[kept] == len(only["sites"])
    assert all(s["verdicts"]["container"] == kept for s in only["sites"])
    other = next(v for v, n in counts.items() if not n)
    none = await call(mcp, "search_sites", min_mw=0, profile="container", verdict=[other])
    assert none["total"] == 0 and none["sites"] == [] and none["next_cursor"] is None
    assert none["verdict_counts"]["container"] == counts  # the summary ignores the verdict filter


async def test_a_verdict_filter_without_a_profile_is_a_tool_error(seeded: None, mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match="wybranego profilu"):
        await call(mcp, "search_sites", min_mw=0, verdict=["green"])


async def test_paging_with_the_cursor_visits_every_site_once(seeded: None, mcp: FastMCP) -> None:
    first = await call(mcp, "search_sites", min_mw=0, limit=1)
    assert len(first["sites"]) == 1 and first["next_cursor"]
    second = await call(mcp, "search_sites", min_mw=0, limit=1, cursor=first["next_cursor"])
    assert len(second["sites"]) == 1 and second["next_cursor"] is None
    assert {first["sites"][0]["site_id"], second["sites"][0]["site_id"]} == {
        s["site_id"] for s in (await call(mcp, "search_sites", min_mw=0))["sites"]
    }


async def test_max_per_gmina_keeps_the_best_site_of_each_gmina(
    seeded: None, places: None, mcp: FastMCP
) -> None:
    every = await call(mcp, "search_sites", min_mw=0)
    capped = await call(mcp, "search_sites", min_mw=0, max_per_gmina=1)
    assert every["total"] == 2 and capped["total"] == 1 and capped["caps"] == {"max_per_gmina": 1}
    assert capped["sites"][0]["site_id"] == every["sites"][0]["site_id"]  # the better one survives
    assert capped["sites"][0]["gmina"] == "Gmina Testowa"


async def test_sites_outside_every_gmina_are_never_hidden_by_the_cap(seeded: None, mcp: FastMCP) -> None:
    capped = await call(mcp, "search_sites", min_mw=0, max_per_gmina=1)
    assert capped["total"] == 2


async def test_top_picks_gives_one_site_per_region(seeded: None, places: None, mcp: FastMCP) -> None:
    result = await call(mcp, "top_picks", profile="container", verdict=["green", "yellow", "red", "manual"])
    assert result["total"] == 1 and result["sites"][0]["voivodeship"] == "mazowieckie"


async def test_names_work_in_place_of_codes_and_ambiguity_lists_the_candidates(
    seeded: None, places: None, mcp: FastMCP
) -> None:
    by_code = await call(mcp, "search_sites", min_mw=0, powiat="1421", voivodeship="14")
    by_name = await call(mcp, "search_sites", min_mw=0, powiat="Powiat Lęborski", voivodeship="MAZOWIECKIE")
    assert by_name["total"] == by_code["total"] == 2
    city = await call(mcp, "search_sites", min_mw=0, powiat="Lębork")  # the exact name is the other powiat
    assert city["total"] == 0
    with pytest.raises(ToolError, match=r"0218.*3025"):
        await call(mcp, "search_sites", powiat="średzki")
    with pytest.raises(ToolError, match="Nie znaleziono"):
        await call(mcp, "search_sites", voivodeship="nieistniejące")


async def test_bulk_get_site_and_compare_sites(seeded: None, mcp: FastMCP) -> None:
    ids = [s["site_id"] for s in (await call(mcp, "search_sites", min_mw=0))["sites"]]
    cards = await call(mcp, "get_site", site_ids=[*ids, "nope"])
    assert [c["site_id"] for c in cards["sites"]] == ids and cards["not_found"] == ["nope"]
    compared = await call(mcp, "compare_sites", site_ids=[*reversed(ids), "nope"])
    assert [c["site_id"] for c in compared["sites"]] == list(reversed(ids)) and compared["not_found"] == [
        "nope"
    ]
    first = compared["sites"][0]
    assert {"score_parts", "grid", "why", "nearest_gpz", "data", "area_ha", "estimated_mw"} <= set(first)
    assert first["score_parts"] and "dist_gpz110_km" in first["grid"]
    assert compared["grid_note"] == context.GRID_NOTE


async def test_min_distance_keeps_one_site_of_a_cluster_and_total_follows(seeded: None, mcp: FastMCP) -> None:
    every = await call(mcp, "search_sites", min_mw=0)
    near = await call(mcp, "search_sites", min_mw=0, min_distance_km=1)  # the seeded sites are 4 km apart
    far = await call(mcp, "search_sites", min_mw=0, min_distance_km=10)
    assert near["total"] == every["total"] == 2 and far["total"] == 1
    assert far["sites"][0]["site_id"] == every["sites"][0]["site_id"]  # the better one survives
    assert far["caps"] == {"min_distance_km": 10} and far["next_cursor"] is None


async def test_min_distance_pages_through_the_spread_list(seeded: None, mcp: FastMCP) -> None:
    first = await call(mcp, "search_sites", min_mw=0, min_distance_km=1, limit=1)
    second = await call(
        mcp, "search_sites", min_mw=0, min_distance_km=1, limit=1, cursor=first["next_cursor"]
    )
    assert first["total"] == 2 and second["next_cursor"] is None
    assert first["sites"][0]["site_id"] != second["sites"][0]["site_id"]


async def test_top_picks_spreads_by_distance_by_default(seeded: None, mcp: FastMCP) -> None:
    result = await call(mcp, "top_picks", profile="container", verdict=["green", "yellow", "red", "manual"])
    assert result["total"] == 1 and result["caps"]["min_distance_km"] == 10
    nearby = await call(
        mcp, "top_picks", profile="container", verdict=["green", "yellow", "red", "manual"],
        per_region=2, min_distance_km=1,
    )  # fmt: skip
    assert nearby["total"] == 2


async def test_connection_costs_uses_the_site_estimated_mw(seeded: None, mcp: FastMCP) -> None:
    site_id = await first_site_id(mcp)
    out = await call(mcp, "connection_costs", site_id=site_id)
    assert out["site_id"] == site_id and out["mw"] > 0
    with pytest.raises(ToolError, match="Nie znaleziono lokalizacji"):
        await call(mcp, "connection_costs", site_id="nie-ma")
