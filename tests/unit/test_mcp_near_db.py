"""`sites_near` against a real PostGIS database, with sites at known distances in EPSG:2180."""

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

from dclh_api.mcp import context, search
from dclh_api.mcp.server import build_mcp
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import score_candidates, write_sites
from tests.unit.test_mcp_tools import call
from tests.unit.test_scoring_run import CFG, X0, Y0, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

NOTE = "TAJNA-NOTATKA-NEAR"
# Seeded sites: a1 = x X0..X0+800 (32 ha), c1 = x X0+5000..X0+5600 (15 ha); both y Y0..Y0+400 / Y0+250.
# The place point sits 1000 m west of a1's edge, so a1 is 1.0 km away and c1 6.0 km.
PLACE = (X0 - 1_000, Y0 + 100)


def box(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"ST_Multi(ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 2180))"


# (teryt, level, name, parent, geometry)
UNITS = (
    ("14", "voivodeship", "mazowieckie", None, box(X0 - 90_000, Y0 - 90_000, X0 + 90_000, Y0 + 90_000)),
    ("1421", "powiat", "powiat lęborski", "14", box(X0 - 9_000, Y0 - 9_000, X0 + 9_000, Y0 + 9_000)),
    ("1422", "powiat", "powiat Lębork", "14", box(X0 - 9_000, Y0 + 20_000, X0 + 9_000, Y0 + 30_000)),
    # 2 km wide, its east edge 1 km from a1.
    ("1421011", "gmina", "Kłodzko Testowe", "1421", box(X0 - 3_000, Y0 - 500, X0 - 1_000, Y0 + 500)),
    # Covers both sites.
    ("1421022", "gmina", "Szeroka", "1421", box(X0 - 100, Y0 - 100, X0 + 6_000, Y0 + 600)),
    ("1421033", "gmina", "Brzeziny", "1421", box(X0 + 30_000, Y0, X0 + 31_000, Y0 + 1_000)),
    ("1422011", "gmina", "Brzeziny", "1422", box(X0 + 30_000, Y0 + 20_000, X0 + 31_000, Y0 + 21_000)),
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
        session.execute(text("DELETE FROM admin_units"))
        for teryt, level, name, parent, geom in UNITS:
            session.execute(
                text(
                    "INSERT INTO admin_units (teryt, level, name, parent_teryt, geom) "
                    f"VALUES (:t, :l, :n, :p, {geom})"
                ),
                {"t": teryt, "l": level, "n": name, "p": parent},
            )
    yield
    with session_scope() as session:
        session.execute(text("DELETE FROM project_cards"))
        session.execute(text("DELETE FROM sites"))
        session.execute(text("DELETE FROM admin_units"))


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


def wgs84(x: float, y: float) -> dict[str, float]:
    with session_scope() as session:
        row = session.execute(
            text(
                "SELECT ST_Y(p) AS lat, ST_X(p) AS lon FROM "
                "(SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:x, :y), 2180), 4326) AS p) q"
            ),
            {"x": x, "y": y},
        ).one()
    return {"lat": row.lat, "lon": row.lon}


def distances(result: dict[str, Any]) -> list[float]:
    return [s["distance_km"] for s in result["sites"]]


async def test_a_point_gives_each_site_its_distance_nearest_first(seeded: None, mcp: FastMCP) -> None:
    result = await call(mcp, "sites_near", min_mw=0, **wgs84(*PLACE))
    assert distances(result) == pytest.approx([1.0, 6.0], abs=0.01)
    assert result["total"] == 2 and result["near"]["kind"] == "point" and result["near"]["radius_km"] == 20
    far_first = await call(mcp, "sites_near", min_mw=0, sort="score", **wgs84(*PLACE))
    scores = [s["score"] for s in far_first["sites"]]
    assert scores == sorted(scores, reverse=True)


async def test_the_radius_cuts_by_distance_to_the_outline(seeded: None, mcp: FastMCP) -> None:
    point = wgs84(*PLACE)
    assert (await call(mcp, "sites_near", min_mw=0, radius_km=3, **point))["total"] == 1
    assert (await call(mcp, "sites_near", min_mw=0, radius_km=0.9, **point))["total"] == 0
    assert (await call(mcp, "sites_near", min_mw=0, radius_km=6.1, **point))["total"] == 2


async def test_the_radius_is_capped_at_100_km(seeded: None, mcp: FastMCP) -> None:
    # 150 km east of the sites: out of reach even with the biggest radius asked for.
    point = wgs84(X0 + 150_000, Y0)
    result = await call(mcp, "sites_near", min_mw=0, radius_km=500, **point)
    assert result["total"] == 0 and result["near"]["radius_km"] == 100
    assert "ograniczono" in result["near"]["radius_capped"]
    inside = await call(mcp, "sites_near", min_mw=0, radius_km=500, **wgs84(X0 + 90_000, Y0))
    assert inside["total"] == 2  # 90 km is within the cap


async def test_a_gmina_by_name_or_code_measures_from_its_border(seeded: None, mcp: FastMCP) -> None:
    for place in ("Kłodzko Testowe", "klodzko testowe", "gmina Kłodzko", "1421011"):
        result = await call(mcp, "sites_near", min_mw=0, place=place)
        assert distances(result) == pytest.approx([1.0, 6.0], abs=0.01), place
        assert result["near"]["teryt"] == "1421011" and result["near"]["level"] == "gmina"


async def test_a_site_inside_the_gmina_is_at_distance_zero(seeded: None, mcp: FastMCP) -> None:
    result = await call(mcp, "sites_near", min_mw=0, place="Szeroka", radius_km=1)
    assert distances(result) == [0.0, 0.0]


async def test_a_powiat_works_and_a_gmina_of_the_same_name_wins(seeded: None, mcp: FastMCP) -> None:
    by_name = await call(mcp, "sites_near", min_mw=0, place="lęborski")
    assert by_name["near"]["teryt"] == "1421" and by_name["near"]["level"] == "powiat"
    assert distances(by_name) == [0.0, 0.0]
    assert (await call(mcp, "sites_near", min_mw=0, place="1421"))["near"]["level"] == "powiat"
    # "Lębork" is a powiat here, and there is no gmina of that name.
    assert (await call(mcp, "sites_near", min_mw=0, place="Lebork"))["near"]["teryt"] == "1422"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"place": "Brzeziny"}, r"kilku gmin: .*1421033, powiat lęborski.*1422011, powiat Lębork"),
        ({"place": "Kutnoo"}, r"Nie znaleziono gminy ani powiatu.*lat i lon"),
        ({"place": "123456"}, r"7 cyfr"),
        ({"place": "9999999"}, r"Nie ma gminy o kodzie TERYT 9999999"),
        ({"place": "Szeroka", "lat": 51.0, "lon": 19.0}, r"nie oba naraz"),
        ({}, r"nie oba naraz"),
        ({"lat": 51.0}, r"nie oba naraz|obu wartości"),
        ({"lat": 40.7, "lon": -74.0}, r"poza Polską"),
        ({"lat": 19.4, "lon": 51.7}, r"poza Polską"),
        ({"place": "Szeroka", "radius_km": 0}, r"większy od zera"),
        ({"place": "Szeroka", "sort": "score", "hide_red": True}, r"wymagają wybranego profilu"),
    ],
)
async def test_bad_places_are_polish_tool_errors(
    seeded: None, mcp: FastMCP, args: dict[str, Any], message: str
) -> None:
    with pytest.raises(ToolError, match=message):
        await call(mcp, "sites_near", min_mw=0, **args)


async def test_the_filters_work_and_verdict_counts_follow_the_radius(seeded: None, mcp: FastMCP) -> None:
    point = wgs84(*PLACE)
    big = await call(mcp, "sites_near", min_mw=0, min_ha=20, **point)
    assert [s["area_ha"] for s in big["sites"]] == [32.0]
    near_only = await call(mcp, "sites_near", min_mw=0, radius_km=3, profile="container", **point)
    assert sum(near_only["verdict_counts"]["container"].values()) == near_only["total"] == 1
    both = await call(mcp, "sites_near", min_mw=0, profile="container", **point)
    assert sum(both["verdict_counts"]["container"].values()) == 2
    assert set(both["sites"][0]["why"]) == {"container"}
    one_per_gmina = await call(mcp, "sites_near", min_mw=0, max_per_gmina=1, **point)
    assert one_per_gmina["total"] == 1 and one_per_gmina["caps"] == {"max_per_gmina": 1}
    full = await call(mcp, "sites_near", min_mw=0, detail="full", **point)
    assert full["sites"][0]["parcels"]["ids"] and "grid_note" in full


async def test_paging_keeps_the_distance_order(seeded: None, mcp: FastMCP) -> None:
    point = wgs84(*PLACE)
    first = await call(mcp, "sites_near", min_mw=0, limit=1, **point)
    assert distances(first) == pytest.approx([1.0], abs=0.01) and first["next_cursor"]
    second = await call(mcp, "sites_near", min_mw=0, limit=1, cursor=first["next_cursor"], **point)
    assert distances(second) == pytest.approx([6.0], abs=0.01) and second["next_cursor"] is None


async def test_the_owner_note_never_leaves(seeded: None, mcp: FastMCP) -> None:
    site_id = (await call(mcp, "sites_near", min_mw=0, place="Szeroka"))["sites"][0]["site_id"]
    with session_scope() as session:
        session.execute(
            text("INSERT INTO project_cards (site_id, stage, owner_note) VALUES (:s, 'analysis', :n)"),
            {"s": site_id, "n": NOTE},
        )
    result = await call(mcp, "sites_near", min_mw=0, place="Szeroka", detail="full")
    assert NOTE not in json.dumps(result)


async def test_search_sites_is_unchanged_and_has_no_distance(seeded: None, mcp: FastMCP) -> None:
    result = await call(mcp, "search_sites", min_mw=0)
    assert result["total"] == 2 and all("distance_km" not in s for s in result["sites"])
    with session_scope() as session, pytest.raises(ToolError, match="wymaga miejsca"):
        search.search_sites(
            session, "https://x", powiat=None, voivodeship=None, min_mw=0, min_ha=0, profile=None,
            hide_red=False, verdicts=None, sort="distance", limit=5, cursor=None,
        )  # fmt: skip


@pytest.mark.parametrize(
    "near",
    [
        search.Near(
            "(SELECT geom FROM admin_units WHERE teryt = :near_teryt)", {"near_teryt": "1421011"}, 20_000
        ),
        search.Near(
            "ST_Transform(ST_SetSRID(ST_MakePoint(:near_lon, :near_lat), 4326), 2180)",
            {"near_lon": 19.4, "near_lat": 51.7},
            20_000,
        ),
    ],
)
def test_the_spatial_index_is_used(seeded: None, near: search.Near) -> None:
    """On a handful of rows the planner prefers a full scan of the covering list index, so the other indexes of
    `sites` are dropped inside a transaction that is rolled back: the filter must be answerable from
    `sites_geom_gix` (the place is a constant, not a per-row expression)."""
    where, params = search._filters(min_mw=0, min_ha=0, powiat=None, voivodeship=None, near=near)
    with session_scope() as session:
        others = session.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'sites' "
                "AND indexname NOT IN ('sites_geom_gix', 'sites_pkey')"
            )
        ).scalars()
        for name in list(others):
            session.execute(text(f'DROP INDEX "{name}"'))
        session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0] for row in session.execute(text(f"EXPLAIN SELECT site_id FROM sites {where}"), params)
        )
        session.rollback()
    assert "sites_geom_gix" in plan, plan
