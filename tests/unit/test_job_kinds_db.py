"""Two workers split the job queue by kind: a check worker must not claim, or fail, a search worker's jobs."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text

from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow, RunRow
from dclh_pipeline.jobs import claim_next, enqueue, fail_interrupted

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]

# `enqueue` only accepts kinds that have a handler; any two of them stand in for "search" and "check".
SEARCH, CHECK = "plan_searches", "discover"


@pytest.fixture(autouse=True)
def empty_queue() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            session.execute(text("DELETE FROM jobs"))
            session.execute(text("DELETE FROM runs"))

    wipe()
    yield
    wipe()


def add_running(kind: str) -> tuple[int, int]:
    """A running job with its run row, as `execute` leaves them. Returns (job id, run id)."""
    with session_scope() as session:
        job = enqueue(session, kind, {})
        run = RunRow(kind=kind, status="running")
        session.add(run)
        session.flush()
        job.status, job.run_id = "running", run.id
        return job.id, run.id


def status_of(job_id: int, run_id: int) -> tuple[str, str]:
    with session_scope() as session:
        job, run = session.get(JobRow, job_id), session.get(RunRow, run_id)
        assert job is not None and run is not None
        return job.status, run.status


def test_claim_next_takes_only_the_asked_for_kinds() -> None:
    with session_scope() as session:
        older = enqueue(session, CHECK, {}).id
        newer = enqueue(session, SEARCH, {}).id
    with session_scope() as session:
        job = claim_next(session, kinds=[SEARCH])
        assert job is not None and job.id == newer  # the older job is another worker's
    with session_scope() as session:
        assert claim_next(session, kinds=[SEARCH]) is None
        job = claim_next(session, kinds=[CHECK])
        assert job is not None and job.id == older


def test_claim_next_can_exclude_a_kind() -> None:
    with session_scope() as session:
        enqueue(session, CHECK, {})
        wanted = enqueue(session, SEARCH, {}).id
    with session_scope() as session:
        job = claim_next(session, exclude=[CHECK])
        assert job is not None and job.id == wanted
    with session_scope() as session:
        assert claim_next(session, exclude=[CHECK]) is None


def test_claim_next_without_arguments_takes_any_kind_oldest_first() -> None:
    with session_scope() as session:
        first = enqueue(session, CHECK, {}).id
        enqueue(session, SEARCH, {})
    with session_scope() as session:
        job = claim_next(session)
        assert job is not None and job.id == first


def test_fail_interrupted_leaves_another_workers_running_job_alone() -> None:
    search_job, search_run = add_running(SEARCH)
    check_job, check_run = add_running(CHECK)
    with session_scope() as session:
        assert fail_interrupted(session, kinds=[CHECK]) == 1  # the check worker restarted
    assert status_of(check_job, check_run) == ("failed", "failed")
    assert status_of(search_job, search_run) == ("running", "running")  # the search worker is still busy


def test_fail_interrupted_with_exclude_is_the_mirror_image() -> None:
    search_job, search_run = add_running(SEARCH)
    check_job, check_run = add_running(CHECK)
    with session_scope() as session:
        assert fail_interrupted(session, exclude=[CHECK]) == 1  # the general worker restarted
    assert status_of(search_job, search_run) == ("failed", "failed")
    assert status_of(check_job, check_run) == ("running", "running")


def test_fail_interrupted_closes_an_orphan_run_of_its_own_kind_only() -> None:
    with session_scope() as session:
        mine, other = RunRow(kind=CHECK, status="running"), RunRow(kind=SEARCH, status="running")
        session.add_all([mine, other])
        session.flush()
        mine_id, other_id = mine.id, other.id
    with session_scope() as session:
        fail_interrupted(session, kinds=[CHECK])
    with session_scope() as session:
        run_mine, run_other = session.get(RunRow, mine_id), session.get(RunRow, other_id)
        assert run_mine is not None and run_mine.status == "failed"
        assert run_other is not None and run_other.status == "running"


def test_fail_interrupted_without_arguments_fails_everything_as_before() -> None:
    a = add_running(SEARCH)
    b = add_running(CHECK)
    with session_scope() as session:
        assert fail_interrupted(session) == 2
    assert status_of(*a) == ("failed", "failed") and status_of(*b) == ("failed", "failed")


class Stop(Exception):
    pass


def test_a_worker_that_has_run_a_job_exits_once_idle_so_its_memory_is_given_back() -> None:
    from dclh_pipeline.jobs import work

    with session_scope() as session:
        enqueue(session, "discover", {"sources": ["ekw"]})  # a manual source: runs without a network
    assert work(kinds=["discover"], interval_s=0.01, exit_after_idle_s=0.05) == 1


def test_a_worker_that_has_run_nothing_keeps_waiting() -> None:
    from dclh_pipeline import jobs

    sleeps = {"n": 0}

    def sleep(seconds: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 5:
            raise Stop

    original = jobs.time.sleep
    jobs.time.sleep = sleep  # type: ignore[assignment]
    try:
        with pytest.raises(Stop):  # it is still polling after five idle rounds: restarting would free nothing
            jobs.work(kinds=["discover"], interval_s=0, exit_after_idle_s=0.0)
    finally:
        jobs.time.sleep = original
