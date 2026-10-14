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

from dclh_pipeline.config import load_scoring, load_sources
from dclh_pipeline.db.models import JobRow, SearchRunRow
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import SiteLayers, build_grid_index
from dclh_pipeline.search import SearchResult, run_search
from dclh_pipeline.settings import Settings, get_settings
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.egib import EgibConnector
from dclh_pipeline.sources.osm_power import OsmPowerConnector, to_grid
from dclh_pipeline.sources.prg import PrgConnector

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
    """Search one powiat. Needs saved `prg` and `osm_power` snapshots. Does not write to the DB."""
    started = time.monotonic()
    settings = settings or get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise ValueError("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    cfg = load_scoring()
    store = SnapshotStore(settings.raw_dir)

    prg_snapshot, osm_snapshot = store.latest("prg"), store.latest("osm_power")
    for name, snap in (("prg", prg_snapshot), ("osm_power", osm_snapshot)):
        if snap is None:
            raise ValueError(f"no saved snapshot of {name!r}; run `dclh fetch {name}` first")
    assert prg_snapshot is not None and osm_snapshot is not None

    admin = PrgConnector(config.get("prg")).parse(prg_snapshot)
    units = admin[
        (admin["level"] == "powiat") & (admin["teryt"].astype(str).str.lstrip("0") == teryt.lstrip("0"))
    ]
    if units.empty:
        raise ValueError(f"no powiat with TERYT {teryt!r} in the PRG snapshot")
    area = units.geometry.iloc[0]

    substations, lines = to_grid(OsmPowerConnector(config.get("osm_power")).parse(osm_snapshot))
    grid = build_grid_index(substations, lines, as_of=osm_snapshot.fetched_at, source_id="osm_power")

    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
    ) as client:

        def fetch_parcels(tiles):  # type: ignore[no-untyped-def]
            connector = EgibConnector(config.get("egib"), bboxes=tiles)
            return connector.parse(connector.fetch(client, store))

        result = run_search(area, SiteLayers(grid=grid), cfg, fetch_parcels, parcels_as_of=None)
    return PowiatSearch(
        teryt=str(units.iloc[0]["teryt"]),
        name=str(units.iloc[0]["name"]),
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
