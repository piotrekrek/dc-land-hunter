"""API response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: bool
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


Confidence = Literal["verified", "estimated", "missing"]
GridBadgeLabel = Literal["Opublikowana moc", "Bliskość sieci", "Brak danych"]


class SiteSummary(BaseModel):
    """One row of the ranked list. Scores are 0–100; `score` is what the list sorts on by default."""

    site_id: str
    zone_id: str | None
    area_ha: float
    estimated_mw: float
    score: float
    upside_score: float
    completeness: float = Field(ge=0, le=1)
    excluded: bool
    grid_badge: GridBadgeLabel
    large_campus: bool
    split_suggested: bool
    flags: list[str]
    lon: float  # a point inside the site, WGS84, for the map
    lat: float
    # estimated_mw is area × density from config: a derived value, so it carries its provenance too.
    mw_fact: FactOut


class SitesPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[SiteSummary]
    # Pass as `cursor` to get the next page; null on the last page. Opaque: do not parse it.
    next_cursor: str | None = None


class FactOut(BaseModel):
    """A feature value with its provenance (brief section 4: source_id, as_of, confidence)."""

    value: float | int | bool | str
    source_id: str
    as_of: datetime | None
    confidence: Confidence


class ExclusionOut(BaseModel):
    key: str
    reason_pl: str
    mode: Literal["exclude", "penalty"]
    penalty_points: float


class CategoryOut(BaseModel):
    category: str
    points: float
    max_points: float
    upside_points: float


class ScoreItemOut(BaseModel):
    key: str
    category: str
    label_pl: str
    points: float
    max_points: float
    upside_points: float
    confidence: Confidence
    source_id: str | None
    as_of: datetime | None
    explanation_pl: str
    value: float | str | bool | None


class SiteDetail(SiteSummary):
    parcel_ids: list[str]
    scored_at: datetime
    exclusions: list[ExclusionOut]
    categories: list[CategoryOut]
    items: list[ScoreItemOut]
    features: dict[str, FactOut]
    geometry: dict[str, Any]  # GeoJSON polygon, WGS84
