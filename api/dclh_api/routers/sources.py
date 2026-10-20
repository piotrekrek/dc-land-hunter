from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.schemas import SourceOut, SourcesList, SourceState
from dclh_api.security import admin_user, current_user
from dclh_pipeline.config import load_sources
from dclh_pipeline.db.models import JobRow, SourceRow, UserRow
from dclh_pipeline.jobs import enqueue
from dclh_pipeline.settings import REPO_ROOT, get_settings

router = APIRouter(tags=["sources"], dependencies=[Depends(current_user)])

# Arbitrary key for pg_advisory_xact_lock; only the discover endpoint takes this lock.
DISCOVER_LOCK_KEY = 4_400_101


def latest_discovery(directories: list[Path]) -> tuple[datetime | None, dict[str, str]]:
    """Status per source from the newest discovery report.

    Reports live in the data volume (written by the worker) and in the repo (`data/discovery/`, committed
    after running `dclh discover` from a machine in Poland).
    """
    reports = sorted((p for d in directories for p in d.glob("discovery-*.json")), key=lambda p: p.name)
    if not reports:
        return None, {}
    data: dict[str, Any] = json.loads(reports[-1].read_text(encoding="utf-8"))
    statuses = {s["source_id"]: s["status"] for s in data.get("sources", [])}
    return datetime.fromisoformat(data["finished_at"]), statuses


@router.get("/sources", response_model=SourcesList)
def list_sources(session: Annotated[Session, Depends(db_session)]) -> SourcesList:
    config = load_sources()
    try:
        rows = {r.id: r for r in session.scalars(select(SourceRow))}
        database = True
    except SQLAlchemyError:
        rows, database = {}, False
    settings = get_settings()
    report_time, report_status = latest_discovery(
        [settings.data_dir / "discovery", REPO_ROOT / "data" / "discovery"]
    )

    out: list[SourceOut] = []
    for spec in config.sources:
        row = rows.get(spec.id)
        status: str | None = report_status.get(spec.id)
        checked_at = report_time if status else None
        if (
            row is not None
            and row.discovery_checked_at
            and (checked_at is None or row.discovery_checked_at > checked_at)
        ):
            status, checked_at = row.discovery_status, row.discovery_checked_at
        out.append(
            SourceOut(
                id=spec.id,
                name=spec.name,
                used_for=spec.used_for,
                plan=spec.plan,
                cadence=spec.cadence,
                core=spec.core,
                licence_expected=spec.licence_expected,
                notes=spec.notes,
                manual_url=str(spec.manual_url) if spec.manual_url else None,
                probe_count=len(spec.probes),
                discovery_status=status,
                discovery_checked_at=checked_at,
                state=SourceState(
                    last_attempt_at=row.last_attempt_at,
                    last_success_at=row.last_success_at,
                    last_error=row.last_error,
                    row_count=row.row_count,
                    stale=row.stale,
                )
                if row is not None
                else None,
            )
        )
    return SourcesList(database=database, sources=out)


class DiscoverIn(BaseModel):
    sources: list[str] = Field(default_factory=list, max_length=50)


class JobOut(BaseModel):
    id: int
    kind: str
    status: str


@router.post("/sources/discover", response_model=JobOut, status_code=202)
def run_discovery(
    body: DiscoverIn,
    session: Annotated[Session, Depends(db_session)],
    admin: Annotated[UserRow, Depends(admin_user)],
) -> JobOut:
    """Queue a discovery check ("Uruchom teraz"). Admin only; the worker runs it."""
    known = {s.id for s in load_sources().sources}
    unknown = sorted(set(body.sources) - known)
    if unknown:
        raise HTTPException(422, f"Nieznane źródła: {', '.join(unknown)}")
    # Serialise the check with other requests on this database: the lock is released at commit.
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DISCOVER_LOCK_KEY})
    busy = session.scalars(
        select(JobRow.id).where(JobRow.kind == "discover", JobRow.status.in_(("pending", "running"))).limit(1)
    ).first()
    if busy is not None:
        raise HTTPException(409, "Wykrywanie źródeł już trwa. Poczekaj, aż zakończy się bieżące sprawdzenie.")
    job = enqueue(session, "discover", {"sources": body.sources, "ran_from": "ui"}, requested_by=admin.email)
    session.commit()
    return JobOut(id=job.id, kind=job.kind, status=job.status)
