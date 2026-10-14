"""SQLAlchemy 2 models. Migrations live in `api/alembic/` and are written by hand.

Phase 0 tables: sources, snapshots, runs, jobs. Later phases add the grid, zones, parcels, sites,
scores, events, pipeline and user tables listed in brief section 9.2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
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


class SearchRunRow(Base):
    """Last search attempt per powiat. The nightly planner picks the powiats attempted longest ago."""

    __tablename__ = "search_runs"

    teryt: Mapped[str] = mapped_column(Text, primary_key=True)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    sites: Mapped[int | None] = mapped_column(Integer)
    seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)


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


class SubstationRow(Base):
    """Geometry lives in PostGIS (`geom`, EPSG:2180) and is written by SQL, so it is not mapped here."""

    __tablename__ = "substations"

    osm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    dso: Mapped[str | None] = mapped_column(String(16))
    foreign_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    voltage_tag: Mapped[str | None] = mapped_column(Text)
    cls: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(64), default="osm_power")
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="member", server_default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SessionRow(Base):
    """Login sessions. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id"), Index("ix_sessions_expires", "expires_at"))

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
