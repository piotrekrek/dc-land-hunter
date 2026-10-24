"""Job queue worker. The API and the scheduler insert rows into `jobs`; `dclh worker` executes them in order.

Running everything through one sequential worker means scheduled runs and "Uruchom teraz" never overlap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from dclh_pipeline.check_job import run_check_job
from dclh_pipeline.config import SourceSpec, load_sources
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow, PowiatPriorityRow, RunRow, SearchRunRow, SourceRow
from dclh_pipeline.discovery import discover, save_report
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.powiat_search import (
    merge_priority,
    next_window_end,
    pick_powiats,
    powiats_from_prg,
    queued_powiats,
    record_attempt,
    search_powiat,
    window_closed,
)
from dclh_pipeline.scoring.run import write_sites
from dclh_pipeline.settings import get_settings
from dclh_pipeline.snapshots import SnapshotStore

log = logging.getLogger(__name__)

JobHandler = Callable[[Session, dict[str, Any]], dict[str, Any]]
CHECK_INTERRUPTED_PL = "Sprawdzenie przerwane przez restart serwera. Uruchom je ponownie."


def sync_sources(session: Session, specs: list[SourceSpec]) -> None:
    """Make sure every configured source has a state row (snapshots and runs reference it)."""
    if not specs:
        return
    stmt = insert(SourceRow).values([{"id": s.id} for s in specs]).on_conflict_do_nothing()
    session.execute(stmt)


def _job_discover(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    config = load_sources()
    wanted = set(payload.get("sources") or [])
    specs = [s for s in config.sources if not wanted or s.id in wanted]
    sync_sources(session, config.sources)
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=1,  # discovery reports failures; it doesn't need to push through them
    ) as client:
        report = discover(specs, client, ran_from=payload.get("ran_from", "worker"))
    json_path, _ = save_report(report, settings.data_dir / "discovery")
    checked_at = datetime.fromisoformat(report.finished_at)
    for result in report.sources:
        row = session.get(SourceRow, result.source_id)
        if row is not None:
            row.discovery_status = result.status.value
            row.discovery_checked_at = checked_at
    counts: dict[str, int] = {}
    for result in report.sources:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
    return {"report": str(json_path), "status_counts": counts}


def _job_search(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Search one powiat and replace its sites. A failed attempt is recorded, so the planner moves on."""
    teryt = str(payload["powiat"])
    now = datetime.now(UTC)
    if window_closed(payload, now):
        return {"powiat": teryt, "skipped": "after the night window closed"}
    try:
        found = search_powiat(teryt)
    except Exception as exc:
        # The job's own session is rolled back on failure, so the attempt is recorded in a session of its own.
        with session_scope() as attempt:
            record_attempt(attempt, teryt, ok=False, at=now, error=str(exc)[:500])
        raise
    count = write_sites(session, found.result.scored, scored_at=found.scored_at, powiat=teryt)
    record_attempt(session, teryt, ok=True, at=now, sites=count, seconds=round(found.seconds, 1))
    report = found.result.report
    return {
        "powiat": teryt,
        "name": found.name,
        "sites": count,
        "scored": report.scored,
        "excluded": report.excluded,
        "parcels_fetched": report.parcels_fetched,
        "seconds": round(found.seconds, 1),
    }


def _job_plan_searches(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Queue the powiats attempted longest ago, each with a deadline at the end of the night window."""
    limit = int(payload.get("limit", 8))
    now = datetime.now(UTC)
    deadline = next_window_end(now, hour=int(payload.get("window_end_hour", 5)))
    snapshot = SnapshotStore(get_settings().raw_dir).latest("prg")
    if snapshot is None:
        raise ValueError("no saved snapshot of 'prg'; run `dclh fetch prg` first (it lists the powiats)")
    saved = session.scalars(
        select(PowiatPriorityRow.teryt).order_by(PowiatPriorityRow.added_at, PowiatPriorityRow.teryt)
    )
    priority = merge_priority(payload.get("priority", []), list(saved))
    busy = queued_powiats(session)
    teryts = [t for t in powiats_from_prg(snapshot) if t not in busy]
    runs = list(session.scalars(select(SearchRunRow)))
    last = {row.teryt: row.last_attempt_at for row in runs}
    searched_ok = {row.teryt for row in runs if row.last_ok_at is not None}
    chosen = pick_powiats(teryts, last, limit, priority=priority, searched_ok=searched_ok)
    for teryt in chosen:
        enqueue(
            session, "search", {"powiat": teryt, "not_after": deadline.isoformat()}, requested_by="planner"
        )
    return {"queued": chosen, "not_after": deadline.isoformat(), "already_queued": sorted(busy)}


HANDLERS: dict[str, JobHandler] = {
    "check_parcel": run_check_job,
    "discover": _job_discover,
    "search": _job_search,
    "plan_searches": _job_plan_searches,
}


def enqueue(
    session: Session, kind: str, payload: dict[str, Any] | None = None, requested_by: str | None = None
) -> JobRow:
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind!r}; known: {sorted(HANDLERS)}")
    job = JobRow(kind=kind, payload=payload or {}, requested_by=requested_by)
    session.add(job)
    session.flush()
    return job


def _kind_filter(kinds: Collection[str] | None, exclude: Collection[str]) -> list[ColumnElement[bool]]:
    """Conditions on `JobRow.kind`: only `kinds` when given, never `exclude`; none means every kind."""
    conditions: list[ColumnElement[bool]] = []
    if kinds is not None:
        conditions.append(JobRow.kind.in_(tuple(kinds)))
    if exclude:
        conditions.append(JobRow.kind.not_in(tuple(exclude)))
    return conditions


def claim_next(
    session: Session, kinds: Collection[str] | None = None, exclude: Collection[str] = ()
) -> JobRow | None:
    """Claim the oldest pending job, optionally only of some kinds (a worker that serves one kind)."""
    job = session.scalars(
        select(JobRow)
        .where(JobRow.status == "pending", *_kind_filter(kinds, exclude))
        .order_by(JobRow.created_at, JobRow.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is not None:
        job.status = "running"
        job.started_at = datetime.now(UTC)
    return job


def fail_interrupted(
    session: Session, kinds: Collection[str] | None = None, exclude: Collection[str] = ()
) -> int:
    """Mark jobs left 'running' by a dead worker as failed, and close their runs. Returns how many.

    Call this only at worker start-up. With several workers, pass the kinds this worker serves (the same
    `kinds` and `exclude` as for `claim_next`): another worker's running job is alive, not interrupted.
    Without arguments every running job counts, as when there is one worker.
    """
    now = datetime.now(UTC)
    error = "interrupted: worker stopped before the job finished"
    stale = session.scalars(
        select(JobRow).where(JobRow.status == "running", *_kind_filter(kinds, exclude))
    ).all()
    for job in stale:
        job.status, job.error, job.finished_at = "failed", error, now
        if job.kind == "check_parcel":
            # The page polls the check, not the job: without this it would show "running" for ever.
            session.execute(
                text(
                    "UPDATE parcel_checks SET status = 'failed', finished_at = :now, error = :error "
                    "WHERE job_id = :job AND status IN ('pending', 'running')"
                ),
                {"now": now, "job": job.id, "error": CHECK_INTERRUPTED_PL},
            )
        run_row = session.get(RunRow, job.run_id) if job.run_id is not None else None
        if run_row is not None and run_row.status == "running":
            run_row.status, run_row.finished_at = "failed", now
            run_row.summary = {"error": error}
    # A run can also be left open when the worker died before the job row pointed at it (same kind).
    for run_row in session.scalars(select(RunRow).where(RunRow.status == "running")):
        if not _run_in_scope(run_row.kind, kinds, exclude):
            continue
        run_row.status, run_row.finished_at = "failed", now
        run_row.summary = {"error": error}
    return len(stale)


def _run_in_scope(kind: str, kinds: Collection[str] | None, exclude: Collection[str]) -> bool:
    return (kinds is None or kind in kinds) and kind not in exclude


def execute(job_id: int) -> None:
    with session_scope() as session:
        job = session.get(JobRow, job_id)
        if job is None:
            return
        run = RunRow(kind=job.kind, status="running")
        session.add(run)
        session.flush()
        job.run_id = run.id
        kind, payload = job.kind, dict(job.payload)
    try:
        with session_scope() as session:
            summary = HANDLERS[kind](session, payload)
        status, error = "done", None
    except Exception as exc:
        log.exception("job %s failed", job_id)
        summary, status, error = {}, "failed", f"{type(exc).__name__}: {exc}"
    with session_scope() as session:
        job = session.get(JobRow, job_id)
        run_row = session.get(RunRow, run.id)
        now = datetime.now(UTC)
        if job is not None:
            job.status, job.error, job.finished_at = status, error, now
        if run_row is not None:
            run_row.status = "ok" if status == "done" else "failed"
            run_row.finished_at = now
            run_row.summary = summary | ({"error": error} if error else {})


def work(
    *,
    once: bool = False,
    interval_s: float = 10.0,
    kinds: Collection[str] | None = None,
    exclude: Collection[str] = (),
) -> int:
    """Poll for jobs. Returns the number of jobs executed (useful with once=True).

    `kinds` and `exclude` split the queue between workers (the check worker serves only `check_parcel`); with
    neither, the worker takes every kind.
    """
    executed = 0
    with session_scope() as session:
        interrupted = fail_interrupted(session, kinds, exclude)
    if interrupted:
        log.warning("marked %d interrupted job(s) as failed", interrupted)
    while True:
        with session_scope() as session:
            job = claim_next(session, kinds, exclude)
            job_id = job.id if job else None
        if job_id is not None:
            log.info("running job %s", job_id)
            execute(job_id)
            executed += 1
            continue
        if once:
            return executed
        time.sleep(interval_s)
