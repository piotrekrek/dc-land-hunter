"""Job health for admins: what the worker ran, what failed and which powiats are stuck."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.security import admin_user

router = APIRouter(tags=["ops"], dependencies=[Depends(admin_user)])

RECENT_JOBS = 50
ERROR_CHARS = 300  # enough to name the exception and its first detail; the full text stays in the database


class JobCounts(BaseModel):
    """`pending` and `running` count every such job; `done` and `failed` only the last 24 hours."""

    pending: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0


class JobItem(BaseModel):
    id: int
    kind: str
    status: str
    powiat: str | None
    powiat_name: str | None
    requested_by: str | None
    created_at: datetime
    finished_at: datetime | None
    error: str | None


class FailingPowiat(BaseModel):
    """A powiat whose most recent search job failed, so its sites are going stale."""

    teryt: str
    name: str | None
    failed_at: datetime | None
    error: str | None


class OpsJobs(BaseModel):
    counts: JobCounts
    failing_powiats: list[FailingPowiat]
    # What the nav badge shows: failing powiats plus failed jobs of the last 24 h that are not searches
    # (a failed search is already counted through its powiat).
    attention: int
    recent: list[JobItem]


_COUNTS = text(
    """
    SELECT status, count(*) AS n FROM jobs
    WHERE status IN ('pending', 'running') OR created_at > now() - interval '24 hours'
    GROUP BY status
    """
)

_FAILING_POWIATS = text(
    """
    SELECT l.teryt, a.name, l.finished_at AS failed_at, left(l.error, :chars) AS error
    FROM (
        SELECT DISTINCT ON (payload->>'powiat')
               payload->>'powiat' AS teryt, status, finished_at, error
        FROM jobs
        WHERE kind = 'search' AND payload ? 'powiat' AND status IN ('done', 'failed')
        ORDER BY payload->>'powiat', created_at DESC
    ) l
    LEFT JOIN admin_units a ON a.level = 'powiat' AND a.teryt = l.teryt
    WHERE l.status = 'failed'
    ORDER BY l.teryt
    """
)

_FAILED_OTHER_24H = text(
    """
    SELECT count(*) FROM jobs
    WHERE status = 'failed' AND kind <> 'search' AND created_at > now() - interval '24 hours'
    """
)

_RECENT = text(
    """
    SELECT j.id, j.kind, j.status, j.payload->>'powiat' AS powiat, a.name AS powiat_name,
           j.requested_by, j.created_at, j.finished_at, left(j.error, :chars) AS error
    FROM jobs j
    LEFT JOIN admin_units a ON a.level = 'powiat' AND a.teryt = j.payload->>'powiat'
    ORDER BY j.id DESC
    LIMIT :limit
    """
)


@router.get("/ops/jobs", response_model=OpsJobs)
def job_health(session: Annotated[Session, Depends(db_session)]) -> OpsJobs:
    """Admin only. Read-only view of the `jobs` table; `search_runs` is not used because a failed search
    rolls back and never updates it."""
    counts = JobCounts(**{status: int(n) for status, n in session.execute(_COUNTS)})
    failing = [
        FailingPowiat(**row) for row in session.execute(_FAILING_POWIATS, {"chars": ERROR_CHARS}).mappings()
    ]
    other_failed = int(session.execute(_FAILED_OTHER_24H).scalar_one())
    recent = [
        JobItem(**row)
        for row in session.execute(_RECENT, {"chars": ERROR_CHARS, "limit": RECENT_JOBS}).mappings()
    ]
    return OpsJobs(
        counts=counts, failing_powiats=failing, attention=len(failing) + other_failed, recent=recent
    )
