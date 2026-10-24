"""The spike records every step, including failures, and sums cold and warm separately."""

from __future__ import annotations

from dclh_pipeline.parcel_spike import StepTiming, _timed, summary_lines


def test_a_failing_step_is_recorded_not_raised() -> None:
    def boom() -> str:
        raise RuntimeError("GUGiK nie odpowiedział")

    timing = _timed("isok_flood", 1, boom)
    assert not timing.ok and "RuntimeError" in timing.detail and timing.pass_no == 1


def test_a_passing_step_keeps_its_detail() -> None:
    timing = _timed("uldk_by_id", 2, lambda: "1 record(s)")
    assert timing.ok and timing.detail == "1 record(s)" and timing.seconds >= 0


def test_summary_separates_cold_and_warm_and_names_failures() -> None:
    timings = [
        StepTiming("a", 1, 10.0, "", True, 100.0),
        StepTiming("b", 1, 5.0, "", False, 180.0),
        StepTiming("a", 2, 1.0, "", True, 120.0),
    ]
    first, second = summary_lines(timings)
    assert "cold total 15.0 s" in first and "warm total 1.0 s" in first and "peak RSS 180 MB" in first
    assert "b (pass 1)" in second
