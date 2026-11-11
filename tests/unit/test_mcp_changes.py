"""`what_changed` without a database: reading `since`, and the diff `write_sites` stores in `site_history`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from dclh_api.mcp.tools_changes import CLAMPED_NOTE, MAX_DAYS, _net, parse_since
from dclh_pipeline.scoring.history import diff

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def row(
    site_id: str, score: float = 50.0, built: str = "yellow", container: str = "yellow", **kw: object
) -> dict:
    return {
        "site_id": site_id,
        "powiat": "1421",
        "area_ha": 12.0,
        "estimated_mw": 72.0,
        "score": score,
        "verdict_built": built,
        "verdict_container": container,
        "kind": "standard",
        "excluded": False,
    } | kw


@pytest.mark.parametrize(("raw", "days"), [("7d", 7), ("14D", 14), ("2w", 14), (" 30d ", 30)])
def test_relative_periods(raw: str, days: int) -> None:
    since, notes = parse_since(raw, NOW)
    assert (NOW - since).days == days and notes == []


def test_an_iso_date_starts_at_midnight_warsaw_time() -> None:
    since, _ = parse_since("2026-11-04", NOW)
    assert since.isoformat() == "2026-11-04T00:00:00+01:00"


def test_more_than_90_days_is_cut_and_says_so() -> None:
    since, notes = parse_since("400d", NOW)
    assert (NOW - since).days == MAX_DAYS and notes == [CLAMPED_NOTE]


@pytest.mark.parametrize("raw", ["", "yesterday", "0d", "2027-01-01", "-3d"])
def test_bad_or_future_periods_are_polish_errors(raw: str) -> None:
    with pytest.raises(ToolError):
        parse_since(raw, NOW)


def test_diff_unchanged_writes_nothing() -> None:
    old = {"a": row("a"), "b": row("b")}
    assert diff(old, [row("a"), row("b")]) == ([], [])


def test_diff_float_noise_in_the_score_is_not_a_change() -> None:
    assert diff({"a": row("a", 50.0)}, [row("a", 50.01)]) == ([], [])


def test_diff_new_changed_and_dropped() -> None:
    old = {"a": row("a", 50, "yellow"), "b": row("b"), "gone": row("gone")}
    events, dropped = diff(old, [row("a", 61, "green"), row("b"), row("n")])
    by_id = {e["site_id"]: e for e in events}
    assert set(by_id) == {"a", "n"} and dropped == ["gone"]
    assert by_id["n"]["event"] == "new" and by_id["n"]["prev_score"] is None
    assert by_id["a"]["event"] == "changed" and by_id["a"]["score"] == 61
    assert (by_id["a"]["prev_score"], by_id["a"]["prev_verdict_built"]) == (50, "yellow")


def test_diff_a_verdict_alone_is_a_change() -> None:
    events, _ = diff({"a": row("a", 50, "yellow")}, [row("a", 50, "red")])
    assert [e["event"] for e in events] == ["changed"]


def test_diff_ignores_small_and_excluded_sites_and_drops_a_site_that_became_excluded() -> None:
    old = {"a": row("a")}
    events, dropped = diff(old, [row("a", excluded=True), row("s", kind="small"), row("x", excluded=True)])
    assert events == [] and dropped == ["a"]


def ev(kind: str, score: float, built: str = "yellow", **kw: object) -> dict:
    return {
        "event": kind,
        "score": score,
        "verdict_built": built,
        "verdict_container": "yellow",
        "prev_score": None,
        "prev_verdict_built": None,
        "prev_verdict_container": None,
    } | kw


def test_net_new_then_dropped_is_nothing() -> None:
    assert _net([ev("new", 50), ev("dropped", 50)]) is None


def test_net_two_changes_report_the_first_from_and_the_last_to() -> None:
    first = ev(
        "changed", 60, "green", prev_score=50, prev_verdict_built="yellow", prev_verdict_container="yellow"
    )
    second = ev(
        "changed", 40, "red", prev_score=60, prev_verdict_built="green", prev_verdict_container="yellow"
    )
    net = _net([first, second])
    assert net is not None
    assert (net["before"]["built"], net["after"]["built"]) == ("yellow", "red")
    assert (net["before"]["score"], net["after"]["score"]) == (50, 40)


def test_net_dropped_then_back_is_a_change_from_the_dropped_state() -> None:
    net = _net([ev("dropped", 50, "yellow"), ev("new", 70, "green")])
    assert net is not None and net["before"]["built"] == "yellow" and net["after"]["built"] == "green"
