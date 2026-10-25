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
# The deal pipeline stages in board order (brief 8.1). Migration 0011 has the same list in a CHECK;
# the Polish labels live in web/src/lib/stages.ts, keyed by these values.
ProjectStage = Literal["new", "watching", "analysis", "owner_contact", "negotiation", "rejected", "secured"]
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
    # Names of the administrative units holding the point above; null when admin_units has no match.
    gmina: str | None
    powiat: str | None
    voivodeship: str | None
    # estimated_mw is area × density from config: a derived value, so it carries its provenance too.
    mw_fact: FactOut
    # Category per investor profile; null for a site stored before profiles existed, and for parcel checks.
    profile_categories: (
        dict[Literal["built", "container"], Literal["green", "yellow", "red", "manual"]] | None
    ) = None


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


class SiteMapOut(BaseModel):
    """The site outline in metres (EPSG:2180, north up) around `origin_2180`, framed like a check sheet."""

    origin_2180: list[float]
    bbox_m: list[float]
    parcel: list[list[list[float]]]


class ProfileReasonOut(BaseModel):
    key: str
    text_pl: str
    source_id: str | None
    as_of: datetime | None


class ProfileToCheckOut(BaseModel):
    key: str
    text_pl: str
    blocking: bool


class ProfileVerdictOut(BaseModel):
    """One investor profile's verdict: green / yellow / red, or manual when a missing answer decides it."""

    profile: Literal["built", "container"]
    category: Literal["green", "yellow", "red", "manual"]
    score: float
    blockers: list[ProfileReasonOut]
    to_check: list[ProfileToCheckOut]
    positives: list[ProfileReasonOut]


class SiteDetail(SiteSummary):
    parcel_ids: list[str]
    scored_at: datetime
    exclusions: list[ExclusionOut]
    categories: list[CategoryOut]
    items: list[ScoreItemOut]
    features: dict[str, FactOut]
    geometry: dict[str, Any]  # GeoJSON polygon, WGS84
    map: SiteMapOut
    profiles: dict[str, ProfileVerdictOut] | None = None  # None for a site stored before profiles existed
    # Per the requesting user (watched) and shared by the team (stage, note); only the site card fills them.
    watched: bool = False
    stage: ProjectStage | None = None
    project_note: str | None = None


class WatchState(BaseModel):
    watched: bool


class ProjectBody(BaseModel):
    stage: ProjectStage
    # Omitted keeps the stored note; null or "" clears it.
    owner_note: str | None = Field(default=None, max_length=2000)


class ProjectState(BaseModel):
    stage: ProjectStage
    owner_note: str | None
    updated_at: datetime


class CoverageArea(BaseModel):
    """Kept sites of one administrative area (not excluded) and how many of them have data.

    `with_data`, `total` and `share` follow the requested item: sites for a single item,
    score items for `all`. `by_item` is always filled, so one response serves the table.
    """

    teryt: str
    name: str
    sites: int
    with_data: int
    total: int
    share: float = Field(ge=0, le=1)
    by_item: dict[str, float]  # share of the area's kept sites with data, per tracked item
    geometry: dict[str, Any] | None = None  # GeoJSON, WGS84, simplified for display; only when asked for


class CoverageOut(BaseModel):
    level: Literal["wojewodztwo", "powiat", "gmina"]
    item: str
    areas: list[CoverageArea]
