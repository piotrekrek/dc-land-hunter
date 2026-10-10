"""Job queue worker. The API and the scheduler insert rows into `jobs`; `dclh worker` executes them in order.

Running everything through one sequential worker means scheduled runs and "Uruchom teraz" never overlap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from dclh_pipeline.config import SourceSpec, load_sources
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import JobRow, RunRow, SourceRow
from dclh_pipeline.discovery import discover, save_report
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import get_settings

log = logging.getLogger(__name__)

JobHandler = Callable[[Session, dict[str, Any]], dict[str, Any]]


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


HANDLERS: dict[str, JobHandler] = {"discover": _job_discover}


def enqueue(
    session: Session, kind: str, payload: dict[str, Any] | None = None, requested_by: str | None = None
) -> JobRow:
    if kind not in HANDLERS:
        raise ValueError(f"unknown job kind {kind!r}; known: {sorted(HANDLERS)}")
    job = JobRow(kind=kind, payload=payload or {}, requested_by=requested_by)
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> JobRow | None:
    job = session.scalars(
        select(JobRow)
        .where(JobRow.status == "pending")
        .order_by(JobRow.created_at, JobRow.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is not None:
        job.status = "running"
        job.started_at = datetime.now(UTC)
    return job


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


def work(*, once: bool = False, interval_s: float = 10.0) -> int:
    """Poll for jobs. Returns the number of jobs executed (useful with once=True)."""
    executed = 0
    while True:
        with session_scope() as session:
            job = claim_next(session)
            job_id = job.id if job else None
        if job_id is not None:
            log.info("running job %s", job_id)
            execute(job_id)
            executed += 1
            continue
        if once:
            return executed
        time.sleep(interval_s)
