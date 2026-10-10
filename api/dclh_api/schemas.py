"""API response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: bool
    postgis_version: str | None
    time: datetime


class SourceState(BaseModel):
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    row_count: int | None = None
    stale: bool = False


class SourceOut(BaseModel):
    id: str
    name: str
    used_for: str
    plan: Literal["mvp", "v2", "manual"]
    cadence: str
    core: bool
    licence_expected: str | None
    notes: str | None
    manual_url: str | None
    probe_count: int
    discovery_status: Literal["verified", "partial", "failed", "not_checked"] | None
    discovery_checked_at: datetime | None
    state: SourceState | None


class SourcesList(BaseModel):
    database: bool
    sources: list[SourceOut]
