"""`site_history` and `what_changed` against a real database."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.mcp import context
from dclh_api.mcp.server import build_mcp
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.scoring.run import ScoredSite, score_candidates, write_sites
from tests.conftest import ALEMBIC_INI
from tests.unit.test_mcp_tools import call
from tests.unit.test_scoring_run import CFG, layers, parcel_sites

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

NOW = datetime.now(UTC)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def wipe() -> None:
    with session_scope() as session:
        for table in ("site_history", "sites", "search_runs", "admin_units"):
            session.execute(text(f"DELETE FROM {table}"))


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    wipe()
    yield
    wipe()


def sites_named(*names: str) -> list[ScoredSite]:
    """Scored sites with the ids given (the fixture has two kept sites; more are copies, moved aside)."""
    base = score_candidates(parcel_sites(), layers(), CFG)
    kept = [s for s in base if not s.score.excluded]
    assert len(kept) >= 2
    out = []
    for i, name in enumerate(names):
        src = kept[i % len(kept)]
        geom = src.site.geom if i < len(kept) else src.site.geom.buffer(0).simplify(0)
        out.append(dataclasses.replace(src, site=dataclasses.replace(src.site, site_id=name, geom=geom)))
    return out


def put(names: list[str], powiat: str, at: datetime) -> None:
    with session_scope() as session:
        write_sites(session, sites_named(*names), scored_at=at, powiat=powiat)


def events(*columns: str) -> list[tuple[Any, ...]]:
    with session_scope() as session:
        cols = ", ".join(columns)
        return [tuple(r) for r in session.execute(text(f"SELECT {cols} FROM site_history ORDER BY id"))]


def tamper(site_id: str, **values: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in values)
    with session_scope() as session:
        session.execute(text(f"UPDATE sites SET {sets} WHERE site_id = :id"), values | {"id": site_id})


def test_first_write_records_new_and_an_unchanged_search_writes_nothing() -> None:
    put(["a", "b"], "1421", days_ago(2))
    assert events("site_id", "event") == [("a", "new"), ("b", "new")]
    put(["a", "b"], "1421", days_ago(1))
    assert len(events("id")) == 2


def test_a_verdict_change_and_a_score_change_are_recorded_with_the_previous_values() -> None:
    put(["a", "b"], "1421", days_ago(5))
    old = dict(events("site_id", "score"))["b"]
    tamper("a", verdict_built="red")
    tamper("b", score=old - 12)
    put(["a", "b"], "1421", days_ago(1))
    rows = events("site_id", "event", "prev_verdict_built", "verdict_built", "prev_score", "score")
    changed = {r[0]: r for r in rows if r[1] == "changed"}
    assert set(changed) == {"a", "b"}
    assert changed["a"][2] == "red" and changed["a"][3] != "red"
    assert changed["b"][4] == pytest.approx(old - 12) and changed["b"][5] == pytest.approx(old)


def test_a_dropped_site_is_recorded_with_its_last_values_and_gmina() -> None:
    put(["a", "b"], "1421", days_ago(5))
    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO admin_units (teryt, level, name, geom) VALUES ('1421011', 'gmina', 'Gmina Testowa', "
                "ST_Multi(ST_MakeEnvelope(0, 0, 2000000, 2000000, 2180)))"
            )
        )
    put(["a"], "1421", days_ago(1))
    dropped = [r for r in events("site_id", "event", "gmina", "area_ha", "score") if r[1] == "dropped"]
    assert [(r[0], r[2]) for r in dropped] == [("b", "Gmina Testowa")] and dropped[0][3] > 0


def test_an_empty_search_drops_everything_of_that_powiat_only() -> None:
    put(["a"], "1421", days_ago(5))
    put(["c"], "1422", days_ago(5))
    put([], "1421", days_ago(1))
    assert [(r[0], r[1]) for r in events("site_id", "event")][-1] == ("a", "dropped")
    assert len(events("id")) == 3


def test_migration_downgrades_and_upgrades_and_seeds_a_baseline() -> None:
    put(["a", "b"], "1421", days_ago(5))
    tamper("b", excluded=True)  # an excluded site is not tracked
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "0017")
    with session_scope() as session:
        assert session.scalar(text("SELECT to_regclass('site_history')")) is None
    command.upgrade(cfg, "head")
    assert events("site_id", "event") == [("a", "baseline")]
    # the first nightly run after the deploy records only real differences
    put(["a", "b"], "1421", days_ago(1))
    after = events("site_id", "event")
    assert after == [("a", "baseline"), ("b", "new")]


@contextmanager
def real_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


@pytest.fixture
def mcp(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    monkeypatch.setattr(context, "tool_session", real_session)
    monkeypatch.setattr(
        context, "tool_user", lambda _s: UserRow(id=1, email="ann@example.org", role="member")
    )
    return build_mcp("https://dclh.example")


def story() -> None:
    """30 days ago 1421 is searched (a, b); then a changes; 1422 is searched for the first time; b drops and
    two sites appear in 1421."""
    put(["a", "b"], "1421", days_ago(30))
    tamper("a", verdict_built="red", score=0)
    put(["a", "b"], "1421", days_ago(3))
    put(["c", "d"], "1422", days_ago(2))
    put(["a", "n1", "n2"], "1421", days_ago(1))


async def test_what_changed_reports_each_category_for_a_week(mcp: FastMCP) -> None:
    story()
    r = await call(mcp, "what_changed")
    assert r["counts"] == {
        "new": 2,
        "dropped": 1,
        "verdict_changes": 1,
        "score_jumps": 1,
        "first_searched_powiaty": 1,
    }
    assert {s["site_id"] for s in r["new_sites"]} == {"n1", "n2"}
    assert all(s["url"].endswith(f"/lokalizacje/{s['site_id']}") for s in r["new_sites"])
    (dropped,) = r["dropped_sites"]
    assert dropped["site_id"] == "b" and "url" not in dropped
    (verdict,) = r["verdict_changes"]
    assert verdict["site_id"] == "a" and verdict["verdict_changes"]["built"]["from"] == "red"
    assert verdict["score_change"]["from"] == 0 and verdict["score_change"]["delta"] >= 5
    assert [p["teryt"] for p in r["first_searched_powiaty"]] == ["1422"]
    assert r["first_searched_powiaty"][0]["sites"] == 2
    assert {"gmina", "powiat", "area_ha", "estimated_mw", "score", "verdicts"} <= set(r["new_sites"][0])
    assert r["history_since"] is not None


async def test_the_period_decides_what_is_reported(mcp: FastMCP) -> None:
    story()
    assert (await call(mcp, "what_changed", since=days_ago(0.5).isoformat()))["counts"] == {
        "new": 0,
        "dropped": 0,
        "verdict_changes": 0,
        "score_jumps": 0,
        "first_searched_powiaty": 0,
    }
    month = await call(mcp, "what_changed", since="45d")
    assert {p["teryt"] for p in month["first_searched_powiaty"]} == {"1421", "1422"}
    assert month["counts"]["new"] == 0  # every site of a first-searched powiat is "first search", not new
    assert (await call(mcp, "what_changed", since=days_ago(10).date().isoformat()))["counts"]["dropped"] == 1


async def test_area_and_profile_filters(mcp: FastMCP) -> None:
    story()
    assert (await call(mcp, "what_changed", powiat="1422"))["counts"]["new"] == 0
    assert (await call(mcp, "what_changed", voivodeship="14"))["counts"]["new"] == 2
    assert (await call(mcp, "what_changed", voivodeship="02"))["counts"]["new"] == 0
    only_container = await call(mcp, "what_changed", profile="container")
    assert only_container["counts"]["verdict_changes"] == 0  # a's container verdict did not change
    assert set(only_container["new_sites"][0]["verdicts"]) == {"built", "container"}


async def test_the_limit_is_per_category_and_capped_and_counts_stay_whole(mcp: FastMCP) -> None:
    story()
    one = await call(mcp, "what_changed", limit=1)
    assert len(one["new_sites"]) == 1 and one["counts"]["new"] == 2
    assert len((await call(mcp, "what_changed", limit=0))["new_sites"]) == 1
    assert len((await call(mcp, "what_changed", limit=10_000))["new_sites"]) == 2


async def test_a_period_before_the_history_says_so(mcp: FastMCP) -> None:
    story()
    r = await call(mcp, "what_changed", since="30d")
    assert (
        any("początkiem historii" in n for n in r["notes"])
        and "Historia zmian jest prowadzona od" in r["note"]
    )
    clamped = await call(mcp, "what_changed", since="400d")
    assert any("90" in n for n in clamped["notes"])


async def test_bad_input_is_a_polish_error(mcp: FastMCP) -> None:
    with pytest.raises(ToolError, match="Nie rozumiem okresu"):
        await call(mcp, "what_changed", since="wczoraj")
    with pytest.raises(ToolError, match="Nie znaleziono"):
        await call(mcp, "what_changed", powiat="nieistniejący")


async def test_an_empty_history_answers(mcp: FastMCP) -> None:
    r = await call(mcp, "what_changed")
    assert r["history_since"] is None and r["counts"]["new"] == 0
    assert json.dumps(r)
