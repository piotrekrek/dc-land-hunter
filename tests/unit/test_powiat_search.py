"""The nightly planner's choices and the search job's deadline, without a database or network."""

from __future__ import annotations

import errno
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dclh_pipeline.jobs import _job_search
from dclh_pipeline.powiat_search import make_room, merge_priority, pick_powiats, window_closed
from dclh_pipeline.powiat_search import next_window_end as window_end
from dclh_pipeline.settings import Settings

WARSAW_NIGHT = datetime(2026, 10, 14, 23, 30, tzinfo=UTC)  # 01:30 in Warsaw (CEST, UTC+2)


def test_never_attempted_powiats_come_first_then_the_stalest() -> None:
    last = {
        "1421": datetime(2026, 10, 13, tzinfo=UTC),
        "1425": datetime(2026, 10, 1, tzinfo=UTC),
        "1427": datetime(2026, 10, 10, tzinfo=UTC),
    }
    teryts = ["1427", "1425", "1421", "1402", "1404"]
    assert pick_powiats(teryts, last, limit=4) == ["1402", "1404", "1425", "1427"]


def test_a_failed_attempt_moves_a_powiat_to_the_back_of_the_queue() -> None:
    # The planner keys on the last attempt, not the last success, so one bad powiat can't be picked forever.
    last = {"1421": datetime(2026, 10, 14, tzinfo=UTC), "1425": datetime(2026, 10, 1, tzinfo=UTC)}
    assert pick_powiats(["1421", "1425"], last, limit=1) == ["1425"]


def test_pick_powiats_is_stable_and_has_no_duplicates() -> None:
    assert pick_powiats(["1404", "1402", "1404"], {}, limit=10) == ["1402", "1404"]
    assert pick_powiats(["1402"], {}, limit=0) == []


def test_unsearched_priority_powiats_go_first_in_the_given_order() -> None:
    last = {"1402": datetime(2026, 10, 1, tzinfo=UTC)}
    teryts = ["1402", "1404", "0805", "1465", "3021"]
    picked = pick_powiats(teryts, last, limit=4, priority=["0805", "3021", "1465"])
    assert picked == ["0805", "3021", "1465", "1404"]


def test_a_priority_powiat_with_a_successful_search_joins_the_rotation() -> None:
    last = {"0805": datetime(2026, 10, 14, tzinfo=UTC)}
    teryts = ["0805", "1404"]
    picked = pick_powiats(teryts, last, limit=2, priority=["0805"], searched_ok={"0805"})
    assert picked == ["1404", "0805"]


def test_a_priority_powiat_that_failed_before_stays_first_until_it_succeeds() -> None:
    last = {"0805": datetime(2026, 10, 14, tzinfo=UTC)}
    picked = pick_powiats(["0805", "1404"], last, limit=1, priority=["0805"], searched_ok=set())
    assert picked == ["0805"]


def test_priority_codes_outside_the_candidates_are_ignored() -> None:
    assert pick_powiats(["1404"], {}, limit=3, priority=["0805", "0805"]) == ["1404"]


def test_cron_priority_comes_before_the_saved_list_without_duplicates() -> None:
    assert merge_priority(["0805", "3021"], ["3021", "1465"]) == ["0805", "3021", "1465"]
    assert merge_priority([], ["1465"]) == ["1465"]
    assert merge_priority([805], []) == ["805"]  # a payload written as numbers still becomes codes


def test_window_ends_at_five_warsaw_time_the_same_night() -> None:
    assert window_end(WARSAW_NIGHT, hour=5) == datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # 05:00 CEST


def test_window_after_five_in_the_morning_moves_to_the_next_night() -> None:
    at_six = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)  # 06:00 Warsaw
    assert window_end(at_six, hour=5) == datetime(2026, 10, 16, 3, 0, tzinfo=UTC)


def test_window_uses_winter_time_in_november() -> None:
    # After the clock change Warsaw is UTC+1, so 05:00 local is 04:00 UTC.
    assert window_end(datetime(2026, 11, 2, 0, 30, tzinfo=UTC), hour=5) == datetime(
        2026, 11, 2, 4, 0, tzinfo=UTC
    )


def test_a_job_without_a_deadline_always_runs() -> None:
    assert window_closed({}, datetime(2030, 1, 1, tzinfo=UTC)) is False


def test_a_job_past_its_deadline_is_skipped_before_any_work() -> None:
    payload = {"powiat": "1421", "not_after": "2026-10-15T03:00:00+00:00"}
    assert window_closed(payload, datetime(2026, 10, 15, 3, 0, tzinfo=UTC)) is True
    assert window_closed(payload, datetime(2026, 10, 15, 2, 59, tzinfo=UTC)) is False
    # The skip happens before the search starts, so no database or snapshot is touched.
    session = MagicMock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("dclh_pipeline.jobs.datetime", _FrozenDatetime(datetime(2026, 10, 15, 4, 0, tzinfo=UTC)))
        result = _job_search(session, payload)
    assert result == {"powiat": "1421", "skipped": "after the night window closed"}
    session.execute.assert_not_called()


class _FrozenDatetime:
    """Stands in for `datetime` in dclh_pipeline.jobs so 'now' is fixed for the test."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz: object = None) -> datetime:
        return self._now


# ----------------------------------------------------------------------------- disk: stop early, don't punish the powiat


def test_a_search_stops_before_it_starts_when_the_disk_is_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dclh_pipeline import powiat_search

    free = {"bytes": 5 * 1024**3}
    monkeypatch.setattr(
        powiat_search.shutil,
        "disk_usage",
        lambda _p: shutil._ntuple_diskusage(100 * 1024**3, 0, free["bytes"]),
    )
    settings = Settings(data_dir=tmp_path, contact_email="test@example.org")
    with pytest.raises(OSError) as raised:
        make_room(settings)
    assert raised.value.errno == errno.ENOSPC and "10 GB" in str(raised.value)
    free["bytes"] = 20 * 1024**3
    make_room(settings)  # enough room: no error


def test_make_room_prunes_the_answer_cache_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dclh_pipeline import powiat_search

    calls: list[int] = []
    monkeypatch.setattr(
        powiat_search.ResponseCache, "prune", lambda _self, max_bytes: calls.append(max_bytes) or (0, 0)
    )
    make_room(Settings(data_dir=tmp_path, contact_email="test@example.org"))
    assert calls == [powiat_search.SEARCH_CACHE_MAX_BYTES]


def test_a_full_disk_is_not_recorded_as_an_attempt_on_the_powiat(monkeypatch: pytest.MonkeyPatch) -> None:
    from dclh_pipeline import jobs

    def full(teryt: str, *args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    def no_db() -> None:
        raise AssertionError("a full disk must not record an attempt")

    monkeypatch.setattr(jobs, "search_powiat", full)
    monkeypatch.setattr(jobs, "session_scope", no_db)
    with pytest.raises(OSError):
        _job_search(MagicMock(), {"powiat": "0801"})


def test_another_os_error_is_still_recorded_as_an_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    from dclh_pipeline import jobs

    def broken(teryt: str, *args: object, **kwargs: object) -> None:
        raise OSError(errno.ECONNRESET, "connection reset")

    recorded: list[dict[str, object]] = []
    scope = MagicMock()
    monkeypatch.setattr(jobs, "search_powiat", broken)
    monkeypatch.setattr(jobs, "session_scope", lambda: scope)
    monkeypatch.setattr(
        jobs, "record_attempt", lambda _s, teryt, **kw: recorded.append({"teryt": teryt, **kw})
    )
    with pytest.raises(OSError):
        _job_search(MagicMock(), {"powiat": "0801"})
    assert recorded and recorded[0]["teryt"] == "0801" and recorded[0]["ok"] is False
