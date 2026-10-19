"""Inputs and outputs of the scoring engine (brief section 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from dclh_pipeline.model.provenance import Confidence


class ZoningKind(StrEnum):
    MPZP_PRODUCTION = "mpzp_production"  # MPZP with P, PU, P/U, U/P (also E or technical infrastructure)
    # plan ogólny, "strefa gospodarcza". Its APP symbol is SP; the name predates the check on real data.
    PLAN_OGOLNY_SG = "plan_ogolny_SG"
    PROCEDURE_IN_PROGRESS = "procedure_in_progress"
    UNKNOWN = "unknown"  # no plan found: unknown, not bad (the Rejestr Urbanistyczny is incomplete)
    OTHER = "other"


class GridBadge(StrEnum):
    """How much to trust the grid score (brief section 6.2)."""

    PUBLISHED = "Opublikowana moc"  # DSO published headroom for this GPZ (verified)
    PROXY = "Bliskość sieci"  # distance proxy (estimated)
    NONE = "Brak danych"  # no grid data at all


@dataclass(frozen=True)
class Fact[T]:
    """A measured or derived value with its provenance. `None` instead of a Fact means "not available"."""

    value: T
    source_id: str
    as_of: datetime | None = None
    confidence: Confidence = Confidence.ESTIMATED


@dataclass(frozen=True)
class SiteFeatures:
    """Everything the scorer knows about a site. A field left as None is *missing*, never "zero"."""

    # land
    area_ha: Fact[float] | None = None
    compactness: Fact[float] | None = None  # Polsby-Popper, 0..1
    parcel_count: Fact[int] | None = None
    expansion_ha: Fact[float] | None = None
    # grid
    dist_line110_km: Fact[float] | None = None
    dist_gpz110_km: Fact[float] | None = None
    dist_node_220_400_km: Fact[float] | None = None
    independent_110_lines_near: Fact[int] | None = None  # distinct 110 kV lines within the N-1 radius
    dist_pse_planned_station_km: Fact[float] | None = None
    closed_switchyard_nearby: Fact[bool] | None = None
    published_gpz_headroom_mw: Fact[float] | None = None  # DSO capacity data for the nearest GPZ
    dist_pse_odb_km: Fact[float] | None = None
    powered_brownfield: Fact[bool] | None = None
    # zoning, connectivity, bonus
    zoning: Fact[ZoningKind] | None = None
    dist_telecom_node_km: Fact[float] | None = None
    dist_big_city_km: Fact[float] | None = None
    dist_heat_network_km: Fact[float] | None = None
    psi_aid_pts: Fact[float] | None = None
    dist_res_cluster_km: Fact[float] | None = None
    env_decision_found: Fact[bool] | None = None
    public_seller: Fact[bool] | None = None
    # constraints (share of the site area, 0..100, or booleans / metres)
    protected_nature_pct: Fact[float] | None = None  # Natura 2000, national park, nature reserve
    flood_10_or_1_pct: Fact[float] | None = None
    flood_0_2_pct: Fact[float] | None = None
    mining_area_pct: Fact[float] | None = None  # obszar górniczy
    mining_terrain_pct: Fact[float] | None = None  # teren górniczy
    forest_pct: Fact[float] | None = None
    soil_class_i_ii_pct: Fact[float] | None = None
    soil_class_iii_pct: Fact[float] | None = None
    on_heritage_register: Fact[bool] | None = None
    residential_min_m: Fact[float] | None = None  # distance to the nearest residential building
    landscape_park_or_ochk: Fact[bool] | None = None
    gas_pipeline_zone: Fact[bool] | None = None
    airport_ols: Fact[bool] | None = None


@dataclass(frozen=True)
class ScoreItem:
    """One explained line of the score breakdown ("dlaczego?")."""

    key: str
    category: str  # grid | zoning | land | connectivity | bonus | penalty
    label_pl: str
    points: float  # contribution to the 0-100 site score (negative for penalties)
    max_points: float
    upside_points: float  # what it would contribute if missing data turned out best-case
    confidence: Confidence
    source_id: str | None
    as_of: datetime | None
    explanation_pl: str
    value: float | str | bool | None = None


@dataclass(frozen=True)
class Exclusion:
    key: str
    reason_pl: str
    mode: str  # exclude | penalty
    penalty_points: float


@dataclass(frozen=True)
class CategoryScore:
    category: str
    points: float
    max_points: float
    upside_points: float


@dataclass(frozen=True)
class SiteScore:
    score: float  # 0-100 after penalties
    upside_score: float  # score if every unknown turned out best-case
    completeness: float  # 0-1: share of scored criteria backed by data
    excluded: bool
    exclusions: list[Exclusion]
    categories: list[CategoryScore]
    items: list[ScoreItem]
    estimated_mw: float | None
    large_campus: bool
    split_suggested: bool
    grid_badge: GridBadge
    flags_pl: list[str] = field(default_factory=list)
