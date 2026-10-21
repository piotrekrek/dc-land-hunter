"""Typed loaders for `config/scoring.yaml` and `config/sources.yaml`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from dclh_pipeline.settings import get_settings


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- scoring.yaml


class Target(_Model):
    mw_min: float
    mw_per_ha: float = Field(gt=0)
    ha_min: float = Field(gt=0)
    ha_ideal: tuple[float, float]
    large_campus_mw: float
    large_site_split_ha: float

    @model_validator(mode="after")
    def _ranges(self) -> Target:
        lo, hi = self.ha_ideal
        if not lo < hi <= self.large_site_split_ha:
            raise ValueError("target.ha_ideal must be (low, high) with low < high <= large_site_split_ha")
        return self


class HardFilter(_Model):
    mode: Literal["exclude", "penalty"]
    penalty_points: float = Field(ge=0)
    threshold_pct: float | None = Field(default=None, ge=0, le=100)
    threshold_m: float | None = Field(default=None, ge=0)


class Weights(_Model):
    grid: float = Field(ge=0)
    zoning: float = Field(ge=0)
    land: float = Field(ge=0)
    connectivity: float = Field(ge=0)
    bonus: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_is_100(self) -> Weights:
        total = self.grid + self.zoning + self.land + self.connectivity + self.bonus
        if abs(total - 100) > 1e-6:
            raise ValueError(f"weights must sum to 100, got {total:g}")
        return self


class GridParams(_Model):
    line110_km_full: float
    line110_km_zero: float
    gpz110_km_full: float
    gpz110_km_zero: float
    two_independent_110_lines_within_km: float
    node_220_400_km_bonus: float
    planned_pse_load_station_km_bonus: float
    closed_switchyard_penalty: float
    published_gpz_headroom_bonus: float
    pse_odb_nearby_km: float
    powered_brownfield_bonus: float
    # Phase 3: a published GPZ counts for a site within this radius;
    # a closed switchyard counts within its own radius.
    published_gpz_km: float = Field(default=5.0, gt=0)
    closed_switchyard_km: float = Field(default=3.0, gt=0)
    # Fuzzy GPZ name matches below this score (0–100) are not used; manual mappings always are.
    gpz_match_min_score: float = Field(default=90.0, ge=0, le=100)

    @model_validator(mode="after")
    def _ranges(self) -> GridParams:
        if not self.line110_km_full < self.line110_km_zero:
            raise ValueError("grid.line110_km_full must be below line110_km_zero")
        if not self.gpz110_km_full < self.gpz110_km_zero:
            raise ValueError("grid.gpz110_km_full must be below gpz110_km_zero")
        return self


class ZoningPoints(_Model):
    mpzp_production: float = Field(ge=0, le=100)
    plan_ogolny_SG: float = Field(ge=0, le=100)
    procedure_in_progress: float = Field(ge=0, le=100)
    unknown: float = Field(ge=0, le=100)
    other: float = Field(ge=0, le=100)


class LandParams(_Model):
    compactness_min: float = Field(ge=0, le=1)
    parcels_full: int = Field(ge=1)
    parcels_zero: int = Field(ge=1)
    expansion_ha_bonus_per_10ha: float = Field(ge=0)

    @model_validator(mode="after")
    def _ranges(self) -> LandParams:
        if not self.parcels_full < self.parcels_zero:
            raise ValueError("land.parcels_full must be below parcels_zero")
        return self


class ConnectivityParams(_Model):
    telecom_node_km_full: float
    telecom_node_km_zero: float
    city_km_full: float
    city_km_zero: float
    cities: list[str]

    @model_validator(mode="after")
    def _ranges(self) -> ConnectivityParams:
        if not self.telecom_node_km_full < self.telecom_node_km_zero:
            raise ValueError("connectivity.telecom_node_km_full must be below telecom_node_km_zero")
        if not self.city_km_full < self.city_km_zero:
            raise ValueError("connectivity.city_km_full must be below city_km_zero")
        return self


class BonusParams(_Model):
    heat_network_km: float
    heat_network_pts: float
    res_cluster_pts: float
    psi_aid_max_pts: float
    psi_aid_full_pct: float = Field(
        gt=0
    )  # the aid rate that earns psi_aid_max_pts (top of the 2022–2027 map)
    res_cluster_km: float
    env_decision_found: float
    public_seller: float


class Penalties(_Model):
    flood_0_2pct: float
    landscape_park_or_ochk: float
    mining_terrain: float
    soil_class_III_pct_over_30: float
    residential_100_300m: float
    gas_pipeline_zone: float
    airport_ols: float


class Prefilter(_Model):
    line110_km: float = Field(gt=0)
    gpz110_km: float = Field(gt=0)
    node_220_400_km: float = Field(gt=0)


class SearchParams(_Model):
    # EGiB carries no land use, so without zoning the search drops town lots by size before clustering.
    min_parcel_ha: float = Field(default=1.0, ge=0)


class ConnectionCost(_Model):
    legal_basis: str
    verified: bool
    application_fee_pln_per_kw: float
    application_fee_max_pln: float
    advance_pln_per_kw: float
    advance_max_pln: float
    security_pln_per_kw: float
    security_applies_up_to_mw: float
    conditions_valid_months: int


class ScoringConfig(_Model):
    target: Target
    hard_filters: dict[str, HardFilter]
    weights: Weights
    grid: GridParams
    zoning: ZoningPoints
    land: LandParams
    connectivity: ConnectivityParams
    bonus: BonusParams
    penalties: Penalties
    prefilter: Prefilter
    search: SearchParams = SearchParams()
    connection_cost: ConnectionCost


# --------------------------------------------------------------------------- sources.yaml


class OgcProbe(_Model):
    kind: Literal["ogc"]
    service: Literal["WFS", "WMS", "WMTS", "CSW"]
    url: HttpUrl


class HttpProbe(_Model):
    kind: Literal["http"]
    method: Literal["GET", "HEAD"] = "GET"
    url: HttpUrl


class PageProbe(_Model):
    kind: Literal["page"]
    url: HttpUrl
    # Links whose text or href contains one of these (case-insensitive) are reported separately.
    keywords: list[str] = Field(default_factory=list)


class ArcgisProbe(_Model):
    kind: Literal["arcgis"]
    url: HttpUrl


class BrowserAction(_Model):
    """One step to run in the page after it loads: type into a field, press a key, or click."""

    action: Literal["fill", "press", "click"]
    selector: str
    value: str | None = None  # text for fill, key name for press


class BrowserProbe(_Model):
    kind: Literal["browser"]
    url: HttpUrl
    actions: list[BrowserAction] = Field(default_factory=list)


Probe = Annotated[
    OgcProbe | HttpProbe | PageProbe | ArcgisProbe | BrowserProbe,
    Field(discriminator="kind"),
]


class SourceSpec(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    used_for: str
    plan: Literal["mvp", "v2", "manual"]
    cadence: Literal["daily", "weekly", "monthly", "static", "none"]
    core: bool = False
    licence_expected: str | None = None
    notes: str | None = None
    manual_url: HttpUrl | None = None
    probes: list[Probe] = Field(default_factory=list)


class SourceDefaults(_Model):
    rate_limit_per_s: float = Field(gt=0, le=10)
    timeout_s: float = Field(gt=0)


class SourcesConfig(_Model):
    defaults: SourceDefaults
    sources: list[SourceSpec]

    @model_validator(mode="after")
    def _unique_ids(self) -> SourcesConfig:
        ids = [s.id for s in self.sources]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate source ids: {sorted(dupes)}")
        return self

    def get(self, source_id: str) -> SourceSpec:
        for spec in self.sources:
            if spec.id == source_id:
                return spec
        raise KeyError(source_id)

    def by_plan(self, plan: str) -> list[SourceSpec]:
        return [s for s in self.sources if s.plan == plan]


# --------------------------------------------------------------------------- aoi.yaml

# TERYT code length per PRG level (voivodeship 02, powiat 0463, gmina 0463011).
TERYT_LENGTH: dict[str, int] = {"voivodeship": 2, "powiat": 4, "gmina": 7}


class IncludedUnit(_Model):
    level: Literal["voivodeship", "powiat", "gmina"]
    teryt: str
    # For people reading the file. The build matches on level and TERYT only.
    name: str

    @model_validator(mode="after")
    def _teryt_shape(self) -> IncludedUnit:
        if not (self.teryt.isdigit() and len(self.teryt) == TERYT_LENGTH[self.level]):
            raise ValueError(
                f"{self.level} TERYT must be {TERYT_LENGTH[self.level]} digits, got {self.teryt!r}"
            )
        return self


class AoiConfig(_Model):
    include: list[IncludedUnit] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> AoiConfig:
        keys = [(u.level, u.teryt) for u in self.include]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate AOI include units: {sorted(dupes)}")
        return self


# --------------------------------------------------------------------------- loaders


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_scoring(path: Path | None = None) -> ScoringConfig:
    path = path or get_settings().config_dir / "scoring.yaml"
    return ScoringConfig.model_validate(_read_yaml(path))


def load_sources(path: Path | None = None) -> SourcesConfig:
    path = path or get_settings().config_dir / "sources.yaml"
    return SourcesConfig.model_validate(_read_yaml(path))


def load_aoi_config(path: Path | None = None) -> AoiConfig:
    path = path or get_settings().config_dir / "aoi.yaml"
    return AoiConfig.model_validate(_read_yaml(path) or {})
