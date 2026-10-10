"""SQLAlchemy 2 models. Migrations live in `api/alembic/` and are written by hand.

Phase 0 tables: sources, snapshots, runs, jobs. Later phases add the grid, zones, parcels, sites,
scores, events, pipeline and user tables listed in brief section 9.2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceRow(Base):
    """Live state of each source. Static facts (name, plan, probes) stay in config/sources.yaml."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int | None] = mapped_column(Integer)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    discovery_status: Mapped[str | None] = mapped_column(String(16))
    discovery_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    __table_args__ = (Index("ix_snapshots_source_fetched", "source_id", "fetched_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    good: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class RunRow(Base):
    """One pipeline run. `summary` holds the data-quality report (brief section 5, step 10)."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class JobRow(Base):
    """Work queue polled by the worker ("Uruchom teraz" and scheduled runs). No message broker."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    requested_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"))
