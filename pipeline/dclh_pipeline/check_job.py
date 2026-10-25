"""The `check_parcel` job: run a parcel check and store its progress and result (PLAN.md, Sprawdź działkę).

The API writes a `parcel_checks` row (status `pending`) and enqueues the job. The handler commits the step
list after every step, in its own short transactions, because `jobs.execute` runs a handler inside one
transaction and the page would otherwise see nothing until the end. Results are saved per parcel, so a check
that fails on its second parcel still shows the first.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from shapely import wkb
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.config import load_profiles, load_scoring, load_sources
from dclh_pipeline.db import session_scope
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.layers import cached_static_layers, release_static_layers
from dclh_pipeline.parcel_check import (
    STEP_LABELS_PL,
    STEP_ORDER,
    CheckContext,
    ParcelCheck,
    ResolvedParcel,
    check_parcel,
    load_aoi,
    resolve_parcels,
)
from dclh_pipeline.parcel_input import ParcelInput, ParsedInputs
from dclh_pipeline.response_cache import ResponseCache
from dclh_pipeline.scoring.run import to_jsonable
from dclh_pipeline.settings import Settings, get_settings
from dclh_pipeline.snapshots import SnapshotStore

log = logging.getLogger(__name__)

KIND = "check_parcel"
CACHE_TTL_DAYS = 40  # as the nightly search: a repeat check reads last answers instead of asking again

# Seconds per step when there is no history yet: the cold numbers of the spike (STATUS.md, 24.10.2026) for
# the
# small box a check uses. `static` is the first check after a worker start; later ones read it from memory.
DEFAULT_STEP_SECONDS = {
    "static": 15.0,
    "parcel": 0.0,
    "neighbours": 8.0,
    "flood": 6.0,
    "mining": 4.0,
    "bdot10k": 4.0,
    "zoning": 2.0,
    "kimpzp": 0.3,
    "kiug": 1.5,
    "scoring": 1.0,
}
HISTORY = 10  # runs per step whose median is the estimate

# The static layers need free memory (the OSM parse refuses to start below 800 MB available on the host),
# which a nightly search can use up for a while. A check waits for it a few minutes before it gives up.
MEMORY_ATTEMPTS = 6
MEMORY_WAIT_S = 30.0
STATIC_IDLE_S = 600.0  # the check worker gives the static layers back after this long without a check
CHECK_MEMORY_PL = (
    "Na serwerze brakuje teraz wolnej pamięci (zwykle trwa nocne przeszukiwanie powiatów). "
    "Spróbuj za kilka minut."
)


def initial_steps(parcels: Sequence[ResolvedParcel], *, static_cached: bool) -> list[dict[str, Any]]:
    """The whole step list up front: the page shows what is coming and a progress bar from the first poll."""
    steps: list[dict[str, Any]] = [
        _step(
            None,
            "static",
            status="done" if static_cached else "waiting",
            seconds=0.0 if static_cached else None,
        )
    ]
    for index, parcel in enumerate(parcels):
        for key in STEP_ORDER:
            if key == "static":
                continue
            done = key == "parcel"
            steps.append(
                _step(
                    index,
                    key,
                    status="done" if done else "waiting",
                    seconds=0.0 if done else None,
                    finding=f"{parcel.commune}, obręb {parcel.obreb}." if done else None,
                )
            )
    return steps


def waiting_steps(parcels: int) -> list[dict[str, Any]]:
    """The step list a check has from the moment it is created, before any worker looks at it.

    The page shows a progress bar and what is coming at once, instead of "0 z 0". The job replaces it when it
    starts (the parcels are resolved by then, so the first step of each is already done).
    """
    steps = [_step(None, "static")]
    for index in range(parcels):
        steps += [_step(index, key) for key in STEP_ORDER if key != "static"]
    return steps


def _step(
    parcel: int | None,
    key: str,
    *,
    status: str = "waiting",
    seconds: float | None = None,
    finding: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "parcel": parcel,
        "key": key,
        "label_pl": STEP_LABELS_PL[key],
        "status": status,
        "seconds": seconds,
        "finding_pl": finding,
        "error": error,
    }


class DbCheckReporter:
    """Writes each step change to `parcel_checks.steps` and records the timings for the estimate."""

    def __init__(self, check_id: str, steps: list[dict[str, Any]], cache: ResponseCache | None) -> None:
        self.check_id, self.steps, self.cache = check_id, steps, cache
        self._misses: dict[tuple[int | None, str], int] = {}

    def _find(self, parcel: int | None, key: str) -> dict[str, Any]:
        return next(s for s in self.steps if s["parcel"] == parcel and s["key"] == key)

    def _save(self) -> None:
        with session_scope() as session:
            session.execute(
                text("UPDATE parcel_checks SET steps = CAST(:steps AS jsonb) WHERE id = :id"),
                {"steps": json.dumps(self.steps, ensure_ascii=False), "id": self.check_id},
            )

    def note(self, parcel: int | None, key: str, text_pl: str) -> None:
        """A short remark on a running step ("Czekam na wolną pamięć…"), visible to the page at once."""
        self._find(parcel, key)["finding_pl"] = text_pl
        self._save()

    def step_start(self, parcel: int | None, key: str) -> None:
        self._find(parcel, key)["status"] = "running"
        self._misses[(parcel, key)] = self.cache.misses if self.cache else 0
        self._save()

    def step_end(
        self,
        parcel: int | None,
        key: str,
        *,
        ok: bool,
        seconds: float,
        finding: str | None = None,
        error: str | None = None,
    ) -> None:
        step = self._find(parcel, key)
        step.update(status="done" if ok else "failed", seconds=seconds, finding_pl=finding, error=error)
        self._save()
        if ok:
            warm = self.cache is not None and self.cache.misses == self._misses.get((parcel, key), 0)
            with session_scope() as session:
                session.execute(
                    text("INSERT INTO check_step_stats (step_key, warm, seconds) VALUES (:k, :w, :s)"),
                    {"k": key, "w": warm, "s": seconds},
                )


def estimate_seconds(session: Session, parcels: int, *, static_cached: bool) -> dict[str, Any]:
    """Expected seconds for a check: the median of the last runs of each step, else the spike's defaults.

    The answers for a new box are never in the response cache, so only BDOT10k (one zip per powiat) and the
    static layers (in memory) can be warm. A step that ran warm before is expected to run warm again.
    """
    total = 0.0
    for key in STEP_ORDER:
        if key == "static" and static_cached:
            continue
        rows = session.execute(
            text("SELECT seconds FROM check_step_stats WHERE step_key = :k ORDER BY at DESC LIMIT :n"),
            {"k": key, "n": HISTORY},
        ).scalars()
        seconds = [float(v) for v in rows]
        per_parcel = 1 if key == "static" else parcels
        total += (statistics.median(seconds) if seconds else DEFAULT_STEP_SECONDS[key]) * per_parcel
    return {"seconds": round(total), "from_history": total > 0}


def create_check(session: Session, requested_by: str, inputs: list[dict[str, Any]]) -> str:
    """Insert a pending check and enqueue its job. Returns the check id. The caller commits."""
    from dclh_pipeline.jobs import enqueue

    check_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO parcel_checks (id, requested_by, status, inputs, steps) "
            "VALUES (:id, :by, 'pending', CAST(:inputs AS jsonb), CAST(:steps AS jsonb))"
        ),
        {
            "id": check_id,
            "by": requested_by,
            "inputs": json.dumps(inputs, ensure_ascii=False),
            "steps": json.dumps(waiting_steps(len(inputs)), ensure_ascii=False),
        },
    )
    job = enqueue(session, KIND, {"check_id": check_id}, requested_by=requested_by)
    session.execute(
        text("UPDATE parcel_checks SET job_id = :job WHERE id = :id"), {"job": job.id, "id": check_id}
    )
    return check_id


def _parsed(inputs: list[dict[str, Any]]) -> ParsedInputs:
    items = []
    for entry in inputs:
        if entry.get("kind") == "point":
            items.append(
                ParcelInput("point", entry.get("text", ""), lat=float(entry["lat"]), lon=float(entry["lon"]))
            )
        else:
            items.append(ParcelInput("id", entry["parcel_id"], parcel_id=entry["parcel_id"]))
    return ParsedInputs(items=items)


_INSERT_SITE = text(
    """
    INSERT INTO parcel_check_sites (
        check_id, n, site_id, powiat, parcel_ids, zone_id, area_ha, compactness, estimated_mw, score,
        upside_score, completeness, excluded, grid_badge, large_campus, split_suggested, flags, detail,
        scored_at, geom, verdict, outside_aoi, unknowns, next_steps, sheet, layer_ages, parcel, profiles
    ) VALUES (
        :check_id, :n, :site_id, :powiat, :parcel_ids, :zone_id, :area_ha, :compactness, :estimated_mw,
        :score, :upside_score, :completeness, :excluded, :grid_badge, :large_campus, :split_suggested, :flags,
        CAST(:detail AS jsonb), :scored_at, ST_SetSRID(ST_GeomFromWKB(:wkb), 2180), CAST(:verdict AS jsonb),
        :outside_aoi, CAST(:unknowns AS jsonb), CAST(:next_steps AS jsonb), CAST(:sheet AS jsonb),
        CAST(:layer_ages AS jsonb), CAST(:parcel AS jsonb), CAST(:profiles AS jsonb)
    )
    ON CONFLICT (check_id, n) DO NOTHING
    """
)


def _json(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False)


def save_result(session: Session, check_id: str, n: int, result: ParcelCheck, scored_at: datetime) -> None:
    site, score, parcel = result.site, result.scored.score, result.parcel
    session.execute(
        _INSERT_SITE,
        {
            "check_id": check_id,
            "n": n,
            "site_id": site.site_id,
            "powiat": parcel.powiat_teryt,
            "parcel_ids": list(site.parcel_ids),
            "zone_id": site.zone_id,
            "area_ha": site.area_ha,
            "compactness": site.compactness,
            "estimated_mw": score.estimated_mw if score.estimated_mw is not None else site.estimated_mw,
            "score": score.score,
            "upside_score": score.upside_score,
            "completeness": score.completeness,
            "excluded": score.excluded,
            "grid_badge": score.grid_badge.value,
            "large_campus": score.large_campus,
            "split_suggested": score.split_suggested,
            "flags": score.flags_pl,
            "detail": _json({"score": score, "features": result.scored.features}),
            "scored_at": scored_at,
            "wkb": wkb.dumps(site.geom),
            "verdict": _json({"kind": result.verdict.kind, "text_pl": result.verdict.text_pl}),
            "outside_aoi": result.outside_aoi,
            "unknowns": _json(result.unknowns),
            "next_steps": _json(result.next_steps),
            "sheet": json.dumps(result.sheet, ensure_ascii=False),
            "layer_ages": _json(result.layer_ages),
            "profiles": _json(result.profiles),
            "parcel": json.dumps(
                {
                    "parcel_id": parcel.parcel_id,
                    "voivodeship": parcel.voivodeship,
                    "county": parcel.county,
                    "commune": parcel.commune,
                    "obreb": parcel.obreb,
                    "powiat_teryt": parcel.powiat_teryt,
                    "registered_area_ha": parcel.area_ha,
                },
                ensure_ascii=False,
            ),
        },
    )


def _finish(check_id: str, *, status: str, error: str | None = None) -> None:
    with session_scope() as session:
        session.execute(
            text("UPDATE parcel_checks SET status = :s, error = :e, finished_at = now() WHERE id = :id"),
            {"s": status, "e": error, "id": check_id},
        )


def build_client(settings: Settings) -> tuple[PoliteClient, ResponseCache]:
    from datetime import timedelta

    config = load_sources()
    cache = ResponseCache(settings.data_dir / "http-cache", timedelta(days=CACHE_TTL_DAYS))
    client = PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
        cache=cache,
    )
    return client, cache


def run_check_job(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Job handler. The passed session is not used: progress needs its own short transactions."""
    check_id = str(payload["check_id"])
    try:
        return _run(check_id)
    except Exception as exc:
        reason = CHECK_MEMORY_PL if isinstance(exc, MemoryError) else f"{type(exc).__name__}: {exc}"[:500]
        _finish(check_id, status="failed", error=reason)
        raise


def _run(check_id: str) -> dict[str, Any]:
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise ValueError("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config, cfg = load_sources(), load_scoring()
    store = SnapshotStore(settings.raw_dir)
    with session_scope() as session:
        row = (
            session.execute(text("SELECT inputs FROM parcel_checks WHERE id = :id"), {"id": check_id})
            .mappings()
            .one()
        )
        session.execute(
            text("UPDATE parcel_checks SET status = 'running', started_at = now() WHERE id = :id"),
            {"id": check_id},
        )
        aoi = load_aoi(session)
    client, cache = build_client(settings)
    with client:
        resolution = resolve_parcels(client, _parsed(list(row["inputs"])), aoi)
        if not resolution.items:
            raise ValueError(" ".join(i.message_pl for i in resolution.issues) or "Nie znaleziono działek.")
        static_cached = _static_is_cached(store)
        steps = initial_steps(resolution.items, static_cached=static_cached)
        reporter = DbCheckReporter(check_id, steps, cache)
        with session_scope() as session:
            session.execute(
                text("UPDATE parcel_checks SET steps = CAST(:s AS jsonb) WHERE id = :id"),
                {"s": json.dumps(steps, ensure_ascii=False), "id": check_id},
            )
        if not static_cached:
            reporter.step_start(None, "static")
        started = time.monotonic()
        static = _static_layers(settings, config, cfg, store, reporter if not static_cached else None)
        if not static_cached:
            reporter.step_end(
                None,
                "static",
                ok=True,
                seconds=round(time.monotonic() - started, 2),
                finding="Dane z migawek wczytane.",
            )
        ctx = CheckContext(
            config=config, cfg=cfg, profiles=load_profiles(), store=store, client=client, static=static
        )
        done = 0
        for index, parcel in enumerate(resolution.items):
            result = check_parcel(parcel, index, ctx, reporter)
            with session_scope() as session:
                save_result(session, check_id, index, result, datetime.now(UTC))
            done += 1
    _finish(check_id, status="done")
    return {"check_id": check_id, "parcels": done, "issues": len(resolution.issues)}


def _static_is_cached(store: SnapshotStore) -> bool:
    from dclh_pipeline.layers import _STATIC_CACHE, snapshot_key

    return _STATIC_CACHE["layers"] is not None and _STATIC_CACHE["key"] == snapshot_key(store)


def _static_layers(
    settings: Settings, config: Any, cfg: Any, store: SnapshotStore, reporter: DbCheckReporter | None
) -> Any:
    """The static layers, waiting for free memory when the host is short of it (see MEMORY_ATTEMPTS)."""
    for attempt in range(1, MEMORY_ATTEMPTS + 1):
        try:
            return cached_static_layers(settings, config, cfg, store)[0]
        except MemoryError:
            if attempt == MEMORY_ATTEMPTS:
                raise
            log.warning(
                "static layers: not enough free memory (attempt %d of %d), waiting", attempt, MEMORY_ATTEMPTS
            )
            if reporter is not None:
                reporter.note(None, "static", "Czekam na wolną pamięć serwera (trwa inne zadanie)…")
            time.sleep(MEMORY_WAIT_S)
    raise AssertionError("unreachable")


def release_when_idle(idle_seconds: float) -> None:
    """`work(on_idle=...)` for the check worker: give the static layers back after a quiet spell."""
    if idle_seconds >= STATIC_IDLE_S and release_static_layers():
        log.info("check worker idle for %.0f s: static layers released", idle_seconds)
