"""Search one powiat end to end, and pick which powiats to search each night (PLAN.md, Phase 1.5).

Shared by `dclh search` (a manual run that prints the funnel) and the `search` and `plan_searches` jobs, so a
nightly run and a manual run use the same code. The planner only chooses; the search job does the work.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from dclh_pipeline.config import SourcesConfig, load_scoring, load_sources
from dclh_pipeline.db.models import JobRow, SearchRunRow
from dclh_pipeline.grid_capacity import load_grid_capacity
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import GeomIndex, Layer, SiteLayers, ZoningLayer, build_grid_index
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Site
from dclh_pipeline.model.zoning import mpzp_outline_kind, plan_ogolny_kind
from dclh_pipeline.scoring.types import Fact
from dclh_pipeline.search import SearchResult, run_search
from dclh_pipeline.settings import Settings, get_settings
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.bdot10k import Bdot10kConnector
from dclh_pipeline.sources.egib import BBox, EgibConnector
from dclh_pipeline.sources.gdos import GdosConnector, landscape_areas, protected_areas
from dclh_pipeline.sources.heat import heat_town_layer
from dclh_pipeline.sources.isok import (
    PROBABILITY_02PCT,
    PROBABILITY_1PCT,
    IsokConnector,
    zones_with_probability,
)
from dclh_pipeline.sources.kimpzp import KimpzpConnector, site_points
from dclh_pipeline.sources.kimpzp import zoning_layer as kimpzp_zoning_layer
from dclh_pipeline.sources.kiug import KiugConnector, site_soil_points, soil_iii_facts
from dclh_pipeline.sources.midas import MidasConnector
from dclh_pipeline.sources.osm_infra import airport_ols_layer, gas_zone_layer, read_infra
from dclh_pipeline.sources.osm_power import OsmPowerConnector, to_grid
from dclh_pipeline.sources.prg import PrgConnector
from dclh_pipeline.sources.psi import load_psi_layer
from dclh_pipeline.sources.ru import RuMpzpConnector, RuPlanOgolnyConnector

WARSAW = ZoneInfo("Europe/Warsaw")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class PowiatSearch:
    teryt: str
    name: str
    result: SearchResult
    scored_at: datetime
    seconds: float


def search_powiat(teryt: str, settings: Settings | None = None) -> PowiatSearch:
    """Search one powiat. Needs saved `prg`, `osm_power` and `gdos` snapshots. Does not write to the DB.

    DSO capacity and the PSE lists are used when a good snapshot exists (`grid_capacity.py`).

    The powiat's flood zones (`isok`, 1% and 0.2%), mining areas (`midas`), residential buildings (`bdot10k`)
    and RU plans (`ru`: MPZP outlines and plan ogólny zones) are downloaded now, like the parcels. A failed
    download fails the search: without them the flood, mining and housing filters would silently not apply and
    zoning would read "unknown", and the nightly
    planner retries the powiat later. KIMPZP is asked per kept site; a gmina server that does not answer
    leaves that point without a plan.
    """
    started = time.monotonic()
    settings = settings or get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise ValueError("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    cfg = load_scoring()
    store = SnapshotStore(settings.raw_dir)

    prg_snapshot, osm_snapshot = store.latest("prg"), store.latest("osm_power")
    gdos_snapshot = store.latest("gdos")
    # gdos is required too: without it the protected-area exclusion would silently never apply.
    for name, snap in (("prg", prg_snapshot), ("osm_power", osm_snapshot), ("gdos", gdos_snapshot)):
        if snap is None:
            raise ValueError(f"no saved snapshot of {name!r}; run `dclh fetch {name}` first")
    assert prg_snapshot is not None and osm_snapshot is not None and gdos_snapshot is not None

    admin = PrgConnector(config.get("prg")).parse(prg_snapshot)
    units = admin[
        (admin["level"] == "powiat") & (admin["teryt"].astype(str).str.lstrip("0") == teryt.lstrip("0"))
    ]
    if units.empty:
        raise ValueError(f"no powiat with TERYT {teryt!r} in the PRG snapshot")
    area = units.geometry.iloc[0]

    substations, lines = to_grid(OsmPowerConnector(config.get("osm_power")).parse(osm_snapshot))
    grid = build_grid_index(substations, lines, as_of=osm_snapshot.fetched_at, source_id="osm_power")
    # Gas pipelines and runways from the same extract: estimated proxies, no public source exists.
    infra = read_infra(osm_snapshot.path)
    gas_layer = gas_zone_layer(infra, cfg.proxies.gas_zone_m, osm_snapshot.fetched_at)
    ols_layer = airport_ols_layer(infra, cfg.proxies.runway_min_m, osm_snapshot.fetched_at)
    # DSO capacity and PSE lists: optional; a source never fetched or imported stays missing in the scores.
    grid_capacity = load_grid_capacity(store, config, substations, cfg)
    gdos = GdosConnector(config.get("gdos")).parse(gdos_snapshot)
    protected_layer = Layer(
        source_id="gdos",
        index=GeomIndex(protected_areas(gdos).geometry),
        as_of=gdos_snapshot.fetched_at,
        confidence=Confidence.VERIFIED,
    )
    # Penalty only: landscape parks and OChK must never reach the protected-area exclusion.
    landscape_layer = Layer(
        source_id="gdos",
        index=GeomIndex(landscape_areas(gdos).geometry),
        as_of=gdos_snapshot.fetched_at,
        confidence=Confidence.VERIFIED,
    )

    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
    ) as client:
        flood_connector = IsokConnector(config.get("isok"), bbox=tuple(area.bounds))
        flood_snapshot = flood_connector.fetch(client, store)
        flood = flood_connector.parse(flood_snapshot)
        store.mark_good(flood_snapshot)
        flood_layer = Layer(
            source_id="isok",
            index=GeomIndex(zones_with_probability(flood, PROBABILITY_1PCT)),
            as_of=flood_snapshot.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        flood_02_layer = Layer(
            source_id="isok",
            index=GeomIndex(zones_with_probability(flood, PROBABILITY_02PCT)),
            as_of=flood_snapshot.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        midas_connector = MidasConnector(config.get("midas"), bbox=tuple(area.bounds))
        midas_snapshot = midas_connector.fetch(client, store)
        midas = midas_connector.parse(midas_snapshot)
        store.mark_good(midas_snapshot)
        mining = {
            kind: Layer(
                source_id="midas",
                index=GeomIndex(midas.geometry[midas["kind"] == kind]),
                as_of=midas_snapshot.fetched_at,
                confidence=Confidence.VERIFIED,
            )
            for kind in ("mining_area", "mining_terrain")
        }
        housing_connector = Bdot10kConnector(config.get("bdot10k"), teryt=teryt)
        housing_snapshot = housing_connector.fetch(client, store)
        housing = housing_connector.parse(housing_snapshot)
        store.mark_good(housing_snapshot)
        # A box can't prove the axis order: the powiat's own buildings must lie in the powiat.
        if housing.empty or not housing.geometry.intersects(area).any():
            raise ValueError(f"BDOT10k: no residential building of powiat {teryt} lies inside it")
        housing_layer = Layer(
            source_id="bdot10k",
            index=GeomIndex(housing.geometry),
            as_of=housing_snapshot.fetched_at,
            confidence=Confidence.VERIFIED,
        )
        forest_layer = Layer(
            source_id="bdot10k",
            index=GeomIndex(housing_connector.parse_forest(housing_snapshot).geometry),
            as_of=housing_snapshot.fetched_at,
            confidence=Confidence.VERIFIED,
        )

        zoning = _ru_zoning_layers(config, client, store, tuple(area.bounds))
        # Bonus layers from saved snapshots (`dclh fetch heat|psi`); a source never fetched stays missing.
        gminas = admin[admin["level"] == "gmina"]
        heat = heat_town_layer(store, config.get("heat"), gminas)
        psi = load_psi_layer(store, config.get("psi"), gminas)

        def sample_zoning(sites: list[Site]) -> tuple[ZoningLayer, dict[str, int]]:
            connector = KimpzpConnector(config.get("kimpzp"), points=site_points(sites))
            snapshot = connector.fetch(client, store)
            answers = connector.parse(snapshot)
            store.mark_good(snapshot)
            counts = {str(k): int(v) for k, v in answers["status"].value_counts().items()}
            return kimpzp_zoning_layer(sites, answers, snapshot.fetched_at), counts

        def sample_soil(sites: list[Site]) -> tuple[dict[str, Fact[float]], dict[str, int]]:
            points = site_soil_points(sites, cfg.search.soil_points_per_site)
            connector = KiugConnector(config.get("kiug"), points=points)
            snapshot = connector.fetch(client, store)
            answers = connector.parse(snapshot)
            store.mark_good(snapshot)
            counts = {str(k): int(v) for k, v in answers["status"].value_counts().items()}
            return soil_iii_facts(answers, snapshot.fetched_at), counts

        def fetch_parcels(tiles):  # type: ignore[no-untyped-def]
            connector = EgibConnector(config.get("egib"), bboxes=tiles)
            return connector.parse(connector.fetch(client, store))

        result = run_search(
            area,
            SiteLayers(
                grid=grid,
                zoning=zoning,
                residential=housing_layer,
                constraints={
                    "protected_nature": protected_layer,
                    "flood_10_or_1": flood_layer,
                    "flood_0_2": flood_02_layer,
                    "landscape_park_or_ochk": landscape_layer,
                    "gas_pipeline_zone": gas_layer,
                    "airport_ols": ols_layer,
                    "forest": forest_layer,
                    **mining,
                },
                capacity=grid_capacity.capacity,
                pse_closed=grid_capacity.pse_closed,
                pse_odb=grid_capacity.pse_odb,
                pse_planned=grid_capacity.pse_planned,
                heat=heat,
                psi=psi,
            ),
            cfg,
            fetch_parcels,
            parcels_as_of=None,
            sample_zoning=sample_zoning,
            sample_soil=sample_soil,
        )
    return PowiatSearch(
        teryt=str(units.iloc[0]["teryt"]),
        name=str(units.iloc[0]["name"]),
        result=result,
        scored_at=datetime.now(UTC),
        seconds=time.monotonic() - started,
    )


def _ru_zoning_layers(
    config: SourcesConfig, client: PoliteClient, store: SnapshotStore, bbox: BBox
) -> tuple[ZoningLayer, ...]:
    """The RU zoning layers of one bbox, in order of precedence: MPZP procedures, then plan ogólny zones.

    An MPZP outline in force has no designations, so it does not decide the zoning (KIMPZP does, ahead of
    these layers). Plan ogólny drafts count only as a procedure for a strefa gospodarcza
    (`plan_ogolny_kind`).
    """
    mpzp = RuMpzpConnector(config.get("ru"), bbox=bbox)
    pog = RuPlanOgolnyConnector(config.get("ru"), bbox=bbox)
    layers = []
    for connector in (mpzp, pog):
        snapshot = connector.fetch(client, store)
        table = connector.parse(snapshot)
        store.mark_good(snapshot)
        if connector is pog:
            kinds = [
                plan_ogolny_kind(z, s) for z, s in zip(table["zone_href"], table["status_href"], strict=True)
            ]
        else:
            kinds = [mpzp_outline_kind(s) for s in table["status_href"]]
        zones = tuple((k, g) for k, g in zip(kinds, table.geometry, strict=True) if k is not None)
        layers.append(ZoningLayer("ru", snapshot.fetched_at, zones))
    return tuple(layers)


def record_attempt(
    session: Session,
    teryt: str,
    *,
    ok: bool,
    at: datetime,
    sites: int | None = None,
    seconds: float | None = None,
    error: str | None = None,
) -> None:
    row = session.get(SearchRunRow, teryt) or SearchRunRow(teryt=teryt, last_ok_at=None)
    row.last_attempt_at = at
    row.status = "ok" if ok else "failed"
    row.sites, row.seconds, row.error = sites, seconds, error
    if ok:
        row.last_ok_at = at
    session.merge(row)


def pick_powiats(teryts: Sequence[str], last_attempt: Mapping[str, datetime], limit: int) -> list[str]:
    """The `limit` powiats attempted longest ago; never-attempted ones first, then by code (stable)."""
    ordered = sorted(set(teryts), key=lambda t: (t in last_attempt, last_attempt.get(t, EPOCH), t))
    return ordered[:limit]


def next_window_end(now: datetime, hour: int) -> datetime:
    """The next local `hour`:00 (Warsaw) after `now`, in UTC. Used as the deadline of a nightly run."""
    local = now.astimezone(WARSAW)
    end = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end.astimezone(UTC)


def window_closed(payload: Mapping[str, Any], now: datetime) -> bool:
    """True when a job's `not_after` deadline has passed. A job without one always runs."""
    not_after = payload.get("not_after")
    return not_after is not None and now >= datetime.fromisoformat(str(not_after))


def powiats_from_prg(snapshot: Snapshot) -> list[str]:
    admin = PrgConnector(load_sources().get("prg")).parse(snapshot)
    return sorted({str(t) for t in admin.loc[admin["level"] == "powiat", "teryt"]})


def queued_powiats(session: Session) -> set[str]:
    """Powiats that already have a search job waiting or running, so the planner doesn't queue them twice."""
    rows = session.scalars(
        select(JobRow.payload).where(JobRow.kind == "search", JobRow.status.in_(("pending", "running")))
    )
    return {str(p.get("powiat")) for p in rows}
