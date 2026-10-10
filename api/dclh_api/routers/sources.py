from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.schemas import SourceOut, SourcesList, SourceState
from dclh_pipeline.config import load_sources
from dclh_pipeline.db.models import SourceRow
from dclh_pipeline.settings import REPO_ROOT, get_settings

router = APIRouter(tags=["sources"])


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
