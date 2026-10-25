"""Search one powiat end to end, and pick which powiats to search each night (PLAN.md, Phase 1.5).

Shared by `dclh search` (a manual run that prints the funnel) and the `search` and `plan_searches` jobs, so a
nightly run and a manual run use the same code. The planner only chooses; the search job does the work.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dclh_pipeline.config import load_scoring, load_sources
from dclh_pipeline.db.models import JobRow, SearchRunRow
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.layers import (
    build_site_layers,
    load_area_layers,
    load_industrial,
    load_static_layers,
    powiat_unit,
    soil_sampler,
    zoning_sampler,
)
from dclh_pipeline.response_cache import ResponseCache
from dclh_pipeline.search import SearchResult, run_search
from dclh_pipeline.settings import Settings, get_settings
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.egib import EgibConnector
from dclh_pipeline.sources.prg import PrgConnector

WARSAW = ZoneInfo("Europe/Warsaw")
# Answers are reused this long, so a monthly refresh reads last month's downloads instead of asking again.
SEARCH_CACHE_TTL = timedelta(days=40)
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

    The layers come from `layers.py`, which a single-parcel check shares. The powiat's flood zones (`isok`, 1%
    and 0.2%), mining areas (`midas`), residential buildings (`bdot10k`) and RU plans (`ru`: MPZP outlines and
    plan ogólny zones) are downloaded now, like the parcels. A failed download fails the search: without them
    the flood, mining and housing filters would silently not apply and zoning would read "unknown", and the
    nightly planner retries the powiat later. KIMPZP is asked per kept site; a gmina server that does not
    answer leaves that point without a plan.
    """
    started = time.monotonic()
    settings = settings or get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise ValueError("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    cfg = load_scoring()
    store = SnapshotStore(settings.raw_dir)

    static = load_static_layers(settings, config, cfg, store)
    unit = powiat_unit(static, teryt)
    area = unit.geometry
    industrial = load_industrial(static, area)

    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
        cache=ResponseCache(settings.data_dir / "http-cache", SEARCH_CACHE_TTL),
    ) as client:
        area_layers = load_area_layers(
            client, config, store, tuple(area.bounds), teryt, powiat_area=area, strict=True
        )

        def fetch_parcels(tiles):  # type: ignore[no-untyped-def]
            connector = EgibConnector(config.get("egib"), bboxes=tiles)
            return connector.parse(connector.fetch(client, store))

        result = run_search(
            area,
            build_site_layers(static, area_layers, industrial),
            cfg,
            fetch_parcels,
            parcels_as_of=None,
            sample_zoning=zoning_sampler(client, config, store),
            sample_soil=soil_sampler(client, config, store, cfg),
        )
    return PowiatSearch(
        teryt=str(unit["teryt"]),
        name=str(unit["name"]),
        result=result,
        scored_at=datetime.now(UTC),
        seconds=time.monotonic() - started,
    )


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


def pick_powiats(
    teryts: Sequence[str],
    last_attempt: Mapping[str, datetime],
    limit: int,
    priority: Sequence[str] = (),
    searched_ok: AbstractSet[str] = frozenset(),
) -> list[str]:
    """The `limit` powiats attempted longest ago; never-attempted ones first, then by code (stable).

    Priority powiats that have not had a successful search yet go before everything else, in the order given.
    Once one has a successful search it joins the normal rotation, so it does not hold a slot every night.
    """
    urgent = [t for t in dict.fromkeys(priority) if t in teryts and t not in searched_ok]
    rest = sorted(
        (t for t in set(teryts) if t not in urgent),
        key=lambda t: (t in last_attempt, last_attempt.get(t, EPOCH), t),
    )
    return (urgent + rest)[:limit]


def powiats_outside_aoi(session: Session) -> set[str]:
    """Powiats whose area does not reach the stored AOI (brief 2.1), for example those east of the Vistula.

    A powiat the river cuts through counts as inside, and one that only touches the AOI's edge as outside.
    Read from the `aoi` table, which the owner can edit, so no list of codes is kept anywhere. With no AOI
    loaded nothing is outside: the planner then searches every powiat, as before the AOI existed.
    """
    rows = session.execute(
        text(
            """
            SELECT p.teryt FROM admin_units p
            WHERE p.level = 'powiat' AND EXISTS (SELECT 1 FROM aoi)
              AND NOT EXISTS (
                SELECT 1 FROM aoi a WHERE ST_Intersects(p.geom, a.geom) AND NOT ST_Touches(p.geom, a.geom)
              )
            """
        )
    )
    return {str(t) for t in rows.scalars()}


def merge_priority(from_payload: Sequence[object], saved: Sequence[str]) -> list[str]:
    """The planner's priority list: the cron payload's codes first, then the ones saved in the Powiaty tab."""
    return list(dict.fromkeys([*(str(t) for t in from_payload), *saved]))


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
