"""Check one parcel with the same layers and the same scorer as the nightly search (PLAN.md, Sprawdź działkę).

A check differs from a search in what it refuses to hide:

- no grid pre-filter and no minimum-size drop: a small or far-away parcel is scored, with its hard-filter
  reasons shown first;
- a layer that fails to load is reported and its criteria stay `missing`; a hard filter that could not be
  evaluated is listed under "Nie wiemy", never read as "fine";
- a parcel outside the area of interest is scored and labelled, not refused.

Everything here is pure computation plus the injected `PoliteClient`; progress goes to a `CheckReporter`,
so the CLI prints it and the job stores it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from pyproj import Transformer
from shapely import wkb, wkt
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.check_sheet import build_sheet, sheet_bounds
from dclh_pipeline.config import ScoringConfig, SourcesConfig
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.layers import (
    AreaLayers,
    StaticLayers,
    build_site_layers,
    load_area_layers,
    load_industrial,
    powiat_unit,
    soil_sampler,
    zoning_sampler,
)
from dclh_pipeline.model.features import Layer, overlap_pct
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Parcel, Site, cluster_sites, expansion_ha
from dclh_pipeline.parcel_input import InputIssue, ParcelInput, ParsedInputs
from dclh_pipeline.scoring.engine import evaluate_hard_filters
from dclh_pipeline.scoring.run import ScoredSite, score_candidates
from dclh_pipeline.scoring.types import Fact, SiteScore
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.egib import EgibConnector
from dclh_pipeline.sources.uldk import (
    UldkParcel,
    by_id_params,
    by_name_params,
    by_xy_params,
    lookup,
    wgs84_to_puwg,
)

# How far around the parcel the area layers reach, and the margin of the sheet. It must cover every distance
# the scorer looks at: the housing hard filter (`residential_within_m`) and the 100–300 m penalty band.
CHECK_MARGIN_M = 500.0
NEIGHBOUR_MARGIN_M = 100.0  # EGiB neighbours only matter within `EXPANSION_GAP_M` (20 m) of the parcel
HOUSING_PENALTY_BAND_M = 300.0

# The order in which `evaluate_hard_filters` reports whether each filter's input data was available.
HARD_FILTER_ORDER = (
    "area_below_min",
    "protected_nature_pct_over",
    "flood_10_or_1_pct",
    "mining_area_active",
    "forest_pct_over",
    "soil_class_I_II_pct_over",
    "heritage_register",
    "residential_within_m",
)
# The loading step that feeds each hard filter. The soil filter (class I-II) has none: KIUG gives only the
# class III share, which feeds a penalty, so that filter stays "nie sprawdzono" whatever happens.
FILTER_LABEL_PL = {
    "area_below_min": "Minimalna powierzchnia",
    "protected_nature_pct_over": "Natura 2000, park narodowy, rezerwat",
    "flood_10_or_1_pct": "Strefa zalewowa",
    "mining_area_active": "Obszar górniczy",
    "forest_pct_over": "Las",
    "soil_class_I_II_pct_over": "Gleby klas I–II",
    "heritage_register": "Rejestr zabytków",
    "residential_within_m": "Zabudowa mieszkaniowa",
}
FILTER_STEP = {
    "flood_10_or_1_pct": "flood",
    "mining_area_active": "mining",
    "forest_pct_over": "bdot10k",
    "residential_within_m": "bdot10k",
}

# Criteria no data feeds in this version (docs/SOURCES.md): UKE cannot be downloaded, environmental
# decisions and public sellers are manual-only. (The PSE planned load stations are loaded since 25.10.2026;
# without that snapshot the criterion is "Nie wiemy" again.)
# They always count as 0 points, so "Nie wiemy" leaves them out: nobody can resolve them by asking. The web
# groups them the same way (`NOT_YET_KEYS` in lib/sites.ts).
NO_DATA_YET = frozenset({"conn_telecom", "bonus_env", "bonus_seller"})

# Step keys and their Polish labels, in the order the loader shows them.
STEP_LABELS_PL = {
    "static": "Sieć, ochrona przyrody i dane z migawek",
    "parcel": "Obrys działki (ULDK)",
    "neighbours": "Sąsiednie działki (EGiB)",
    "flood": "Strefy zalewowe",
    "mining": "Obszary górnicze",
    "bdot10k": "Budynki mieszkalne i lasy (BDOT10k)",
    "zoning": "Plany miejscowe i plan ogólny (RU)",
    "kimpzp": "Plan miejscowy w punkcie działki (KIMPZP)",
    "kiug": "Gleby klasy III (KIUG)",
    "scoring": "Ocena",
}
STEP_ORDER = tuple(STEP_LABELS_PL)

# A site is "worth checking further" from this score with this much of its criteria backed by data. 70 is the
# upper tick of the score ruler (the "strong" threshold the UI already shows).
STRONG_SCORE = 70.0
LOW_COMPLETENESS = 0.7

_TO_WGS = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)


class CheckReporter(Protocol):
    """Hears about each step of a check. `parcel` is the index of the parcel, or None for a shared step."""

    def step_start(self, parcel: int | None, key: str) -> None: ...

    def step_end(
        self,
        parcel: int | None,
        key: str,
        *,
        ok: bool,
        seconds: float,
        finding: str | None = None,
        error: str | None = None,
    ) -> None: ...


class NullCheckReporter:
    def step_start(self, parcel: int | None, key: str) -> None:
        pass

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
        pass


@dataclass(frozen=True)
class ResolvedParcel:
    """A parcel ULDK found: enough to show "Czy to ta działka?" and to start a check."""

    parcel_id: str
    powiat_teryt: str
    voivodeship: str
    county: str
    commune: str
    obreb: str
    geom: BaseGeometry  # EPSG:2180
    area_ha: float
    lon: float
    lat: float
    in_aoi: bool | None  # None: the area of interest is not loaded
    source_text: str  # what the person typed


@dataclass(frozen=True)
class Resolution:
    items: list[ResolvedParcel]
    issues: list[InputIssue]
    dropped: int = 0
    duplicates: int = 0


@dataclass(frozen=True)
class Verdict:
    kind: str  # "odpada" | "wymaga_wyjasnien" | "warto"
    text_pl: str


@dataclass(frozen=True)
class Unknown:
    label_pl: str
    why_pl: str


@dataclass(frozen=True)
class NextStep:
    key: str
    title_pl: str
    copy_text: str | None = None


@dataclass
class StepRecord:
    key: str
    label_pl: str
    status: str = "waiting"  # waiting | running | done | failed
    seconds: float | None = None
    finding_pl: str | None = None
    error: str | None = None


@dataclass
class ParcelCheck:
    parcel: ResolvedParcel
    site: Site
    scored: ScoredSite
    verdict: Verdict
    unknowns: list[Unknown]
    next_steps: list[NextStep]
    steps: list[StepRecord]
    sheet: dict[str, Any]
    layer_ages: dict[str, datetime | None]
    outside_aoi: bool
    failed_steps: list[str] = field(default_factory=list)


def check_buffer_m(cfg: ScoringConfig) -> float:
    """Distance around the parcel the area layers must cover. Never below what the scorer looks at."""
    hf = cfg.hard_filters
    needed = max(HOUSING_PENALTY_BAND_M, hf["residential_within_m"].threshold_m or 0.0)
    return max(CHECK_MARGIN_M, needed)


# ------------------------------------------------------------------------------------------------ resolving


def _powiat_of(parcel_id: str) -> str:
    """The powiat TERYT: the first four digits of the gmina code before the underscore."""
    return parcel_id.split("_", 1)[0][:4]


def _obreb_of(parcel_id: str) -> str:
    parts = parcel_id.split(".")
    return parts[1] if len(parts) >= 3 else ""


def _resolved(record: UldkParcel, source_text: str, aoi: BaseGeometry | None) -> ResolvedParcel:
    if record.wkt is None:
        raise ValueError("ULDK nie zwrócił obrysu działki")
    geom: BaseGeometry = wkt.loads(record.wkt.split(";", 1)[-1])
    point = geom.representative_point()
    lon, lat = _TO_WGS.transform(point.x, point.y)
    return ResolvedParcel(
        parcel_id=record.parcel_id,
        powiat_teryt=_powiat_of(record.parcel_id),
        voivodeship=record.voivodeship,
        county=record.county,
        commune=record.commune,
        obreb=_obreb_of(record.parcel_id),
        geom=geom,
        area_ha=geom.area / 10_000,
        lon=float(lon),
        lat=float(lat),
        in_aoi=None if aoi is None else bool(aoi.intersects(point)),
        source_text=source_text,
    )


def _params(item: ParcelInput) -> dict[str, str]:
    if item.kind == "id" and item.parcel_id:
        return by_id_params(item.parcel_id)
    if item.kind == "point" and item.lat is not None and item.lon is not None:
        x, y = wgs84_to_puwg(item.lat, item.lon)
        return by_xy_params(x, y)
    return by_name_params(item.query or item.text)


def resolve_parcels(
    client: PoliteClient, parsed: ParsedInputs, aoi: BaseGeometry | None = None
) -> Resolution:
    """Ask ULDK for each recognised input. A lookup that fails becomes an issue the person can act on."""
    items: list[ResolvedParcel] = []
    issues = list(parsed.issues)
    seen: set[str] = set()
    duplicates = parsed.duplicates
    for item in parsed.items:
        try:
            records = lookup(client, _params(item))
        except ValueError:
            issues.append(_not_found(item))
            continue
        except Exception as exc:  # a network or service failure is not "no such parcel"
            issues.append(
                InputIssue(
                    item.text, f"Serwer GUGiK nie odpowiedział ({type(exc).__name__}). Spróbuj za chwilę."
                )
            )
            continue
        for record in records[:1]:
            if record.parcel_id in seen:
                duplicates += 1
                continue
            seen.add(record.parcel_id)
            try:
                items.append(_resolved(record, item.text, aoi))
            except ValueError as exc:
                issues.append(InputIssue(item.text, str(exc)))
    return Resolution(items=items, issues=issues, dropped=parsed.dropped, duplicates=duplicates)


def _not_found(item: ParcelInput) -> InputIssue:
    if item.kind == "id":
        return InputIssue(
            item.text,
            f"Nie znaleziono działki {item.text}. Sprawdź identyfikator, np. 306201_1.0002.1573.",
        )
    if item.kind == "point":
        return InputIssue(item.text, "W tym punkcie nie ma działki ewidencyjnej. Wskaż punkt na działce.")
    return InputIssue(
        item.text,
        f"Nie znaleziono działki „{item.text}”. Dopisz obręb albo wklej pełny identyfikator, "
        "np. 306201_1.0002.1573.",
    )


# ------------------------------------------------------------------------------------------------ verdict


def verdict(score: SiteScore) -> Verdict:
    """One sentence, the deciding reason first. Three outcomes, from the existing scorer output only."""
    if score.excluded:
        reasons = [e for e in score.exclusions if e.mode == "exclude"] or score.exclusions
        return Verdict("odpada", f"Odpada: {reasons[0].reason_pl}.")
    missing = [i.label_pl for i in score.items if i.confidence is Confidence.MISSING]
    if score.score >= STRONG_SCORE and score.completeness >= LOW_COMPLETENESS:
        return Verdict(
            "warto",
            f"Warto sprawdzać dalej: wynik {score.score:.0f} pkt, dane kompletne w {score.completeness:.0%}.",
        )
    if score.completeness < LOW_COMPLETENESS:
        what = ", ".join(missing[:3]) if missing else "część kryteriów"
        return Verdict(
            "wymaga_wyjasnien",
            f"Wymaga wyjaśnień: brakuje danych ({what}); po uzupełnieniu do {score.upside_score:.0f} pkt.",
        )
    # Not excluded, data mostly there, but below the "worth it" score: say where the points are missing.
    lost = sorted((i for i in score.items if i.max_points > i.points), key=lambda i: i.points - i.max_points)[
        :2
    ]
    where = "; ".join(i.label_pl for i in lost)
    return Verdict(
        "wymaga_wyjasnien",
        f"Wymaga wyjaśnień: wynik {score.score:.0f} pkt, a „warto” zaczyna się od {STRONG_SCORE:.0f}; "
        f"najwięcej punktów brakuje w: {where}.",
    )


def unknowns(scored: ScoredSite, cfg: ScoringConfig, failed_steps: Sequence[str]) -> list[Unknown]:
    """What the check could not establish: missing criteria, and hard filters that could not be evaluated."""
    out: list[Unknown] = []
    _, known = evaluate_hard_filters(scored.features, cfg)
    for key, available in zip(HARD_FILTER_ORDER, known, strict=True):
        if available:
            continue
        step = FILTER_STEP.get(key)
        why = (
            "nie udało się pobrać danych, więc ten warunek nie został sprawdzony"
            if step in failed_steps
            else "brak publicznego źródła danych, ten warunek nie został sprawdzony"
        )
        label = FILTER_LABEL_PL[key]
        out.append(Unknown(f"Nie sprawdzono: {label[0].lower()}{label[1:]}", why))
    for item in scored.score.items:
        if item.confidence is Confidence.MISSING and item.key not in NO_DATA_YET:
            out.append(Unknown(item.label_pl, item.explanation_pl))
    return out


def next_steps(parcel: ResolvedParcel) -> list[NextStep]:
    """Manual follow-ups no public service can do for us (the app never promises grid capacity)."""
    place = f"działki {parcel.parcel_id} w obrębie {parcel.obreb}, gmina {parcel.commune}"
    return [
        NextStep(
            "mpzp",
            f"Zapytaj gminę {parcel.commune} o MPZP lub plan ogólny dla działki",
            f"Dzień dobry, proszę o informację o przeznaczeniu {place} w miejscowym planie zagospodarowania "
            "przestrzennego lub planie ogólnym.",
        ),
        NextStep(
            "dso",
            "Złóż zapytanie do operatora sieci o moc w najbliższym GPZ",
            f"Zapytanie o możliwość przyłączenia mocy do 110 kV dla {place}.",
        ),
        NextStep("kw", "Sprawdź księgę wieczystą ręcznie w EKW"),
        NextStep("ooss", "Sprawdź decyzje środowiskowe w Bazie OOŚ"),
    ]


# ------------------------------------------------------------------------------------------------ findings


def _pct(value: float) -> str:
    return f"{value:.0f}%".replace(".", ",")


def _ha(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " ha"


def _flood_finding(geom: BaseGeometry, result: Any) -> str:
    share = overlap_pct(geom, result["flood_10_or_1"].index.geoms)
    share_02 = overlap_pct(geom, result["flood_0_2"].index.geoms)
    if share > 0:
        return f"Strefa zalewowa 1%: {_pct(share)} działki."
    if share_02 > 0:
        return f"Poza strefą 1%, ale {_pct(share_02)} działki leży w strefie 0,2%."
    return "Działka poza strefami zalewowymi (1% i 0,2%)."


def _mining_finding(geom: BaseGeometry, result: Any) -> str:
    share = max(overlap_pct(geom, layer.index.geoms) for layer in result.values()) if result else 0.0
    return f"Obszar górniczy: {_pct(share)} działki." if share > 0 else "Poza obszarami górniczymi."


def _bdot_finding(geom: BaseGeometry, result: Any) -> str:
    near = result["housing"].index.nearest_distance_m(geom)
    forest = overlap_pct(geom, result["forest"].index.geoms)
    house = (
        f"najbliższy budynek mieszkalny {near:.0f} m"
        if near is not None
        else "brak budynków mieszkalnych w okolicy"
    )
    return f"{house.capitalize()}; las: {_pct(forest)} działki."


def _zoning_finding(geom: BaseGeometry, result: Any) -> str:
    zones = sum(len(layer.zones) for layer in result)
    if not zones:
        return "Brak planu w rejestrze (rejestr jest niepełny do 30.11.2026, więc to nie znaczy „źle”)."
    return f"W rejestrze: {zones} obrys(ów) planów w okolicy."


# ------------------------------------------------------------------------------------------------ the check


@dataclass(frozen=True)
class CheckContext:
    config: SourcesConfig
    cfg: ScoringConfig
    store: SnapshotStore
    client: PoliteClient
    static: StaticLayers


class _Steps:
    """Records and reports the steps of one parcel."""

    def __init__(self, index: int, reporter: CheckReporter) -> None:
        self.index, self.reporter = index, reporter
        self.records: dict[str, StepRecord] = {
            k: StepRecord(k, STEP_LABELS_PL[k]) for k in STEP_ORDER if k != "static"
        }

    def start(self, key: str) -> None:
        self.records[key].status = "running"
        self.reporter.step_start(self.index, key)

    def end(
        self, key: str, *, ok: bool, started: float, finding: str | None = None, error: str | None = None
    ) -> None:
        record = self.records[key]
        record.status = "done" if ok else "failed"
        record.seconds = round(time.monotonic() - started, 2)
        record.finding_pl, record.error = finding, error
        self.reporter.step_end(self.index, key, ok=ok, seconds=record.seconds, finding=finding, error=error)


class _AreaAdapter:
    """Lets `load_area_layers` report through the check's steps, with a finding built from each result."""

    def __init__(self, steps: _Steps, geom: BaseGeometry) -> None:
        self.steps, self.geom = steps, geom
        self._started: dict[str, float] = {}
        self.failed: list[str] = []

    def start(self, key: str) -> None:
        self._started[key] = time.monotonic()
        self.steps.start(key)

    def finish(self, key: str, *, ok: bool, result: Any = None, error: str | None = None) -> None:
        builders: dict[str, Callable[[BaseGeometry, Any], str]] = {
            "flood": _flood_finding,
            "mining": _mining_finding,
            "bdot10k": _bdot_finding,
            "zoning": _zoning_finding,
        }
        if ok:
            self.steps.end(key, ok=True, started=self._started[key], finding=builders[key](self.geom, result))
        else:
            self.failed.append(key)
            self.steps.end(key, ok=False, started=self._started[key], error=error)


def check_parcel(
    parcel: ResolvedParcel, index: int, ctx: CheckContext, reporter: CheckReporter | None = None
) -> ParcelCheck:
    """Load the layers around one parcel, score it and explain it. A failed layer does not stop the check."""
    reporter = reporter or NullCheckReporter()
    steps = _Steps(index, reporter)
    cfg, config, store, client, static = ctx.cfg, ctx.config, ctx.store, ctx.client, ctx.static
    failed: list[str] = []
    geom = parcel.geom

    # The parcel was resolved before the job started; show that step as done.
    steps.records["parcel"].status, steps.records["parcel"].seconds = "done", 0.0
    steps.records["parcel"].finding_pl = f"{parcel.commune}, obręb {parcel.obreb}, {_ha(parcel.area_ha)}."

    site = _single_site(parcel, cfg)
    margin = check_buffer_m(cfg)
    sheet_box = sheet_bounds(geom, margin)

    # Neighbouring parcels count toward the expansion land (within 20 m of the parcel).
    steps.start("neighbours")
    t0 = time.monotonic()
    expansion: dict[str, Fact[float]] = {}
    neighbours_geoms: list[BaseGeometry] = []
    try:
        near_box = sheet_bounds(geom, NEIGHBOUR_MARGIN_M)
        connector = EgibConnector(config.get("egib"), bboxes=[near_box])
        frame = connector.parse(connector.fetch(client, store))
        ids, geoms = [str(p) for p in frame["parcel_id"]], list(frame.geometry)
        neighbours_geoms = [g for pid, g in zip(ids, geoms, strict=True) if pid != parcel.parcel_id]
        ha = expansion_ha([site], ids, geoms)[site.site_id]
        expansion = {site.site_id: Fact(ha, "egib", None, Confidence.VERIFIED)}
        steps.end(
            "neighbours", ok=True, started=t0, finding=f"Sąsiednie działki w promieniu 20 m: {_ha(ha)}."
        )
    except Exception as exc:
        failed.append("neighbours")
        steps.end("neighbours", ok=False, started=t0, error=f"{type(exc).__name__}: {exc}")

    powiat = powiat_unit(static, parcel.powiat_teryt)
    adapter = _AreaAdapter(steps, geom)
    area: AreaLayers = load_area_layers(
        client,
        config,
        store,
        sheet_box,
        parcel.powiat_teryt,
        powiat_area=powiat.geometry,
        reporter=adapter,
        strict=False,
        clip=sheet_box,
    )
    failed.extend(adapter.failed)

    layers = build_site_layers(static, area, load_industrial(static, geom.buffer(margin)))
    layers = replace(layers, expansion_ha=expansion)

    steps.start("scoring")
    t0 = time.monotonic()
    scored = score_candidates([site], layers, cfg)[0]
    steps.records["scoring"].status = "running"

    # KIMPZP and KIUG are one point request each, so a check always asks them, also for an excluded parcel.
    sampled = layers
    t1 = time.monotonic()
    steps.start("kimpzp")
    try:
        layer, counts = zoning_sampler(client, config, store)([site])
        sampled = replace(sampled, zoning=(layer, *sampled.zoning))
        steps.end(
            "kimpzp",
            ok=True,
            started=t1,
            finding=f"Zapytano gminny serwer planów w punkcie działki ({sum(counts.values())} odp.).",
        )
    except Exception as exc:
        failed.append("kimpzp")
        steps.end("kimpzp", ok=False, started=t1, error=f"{type(exc).__name__}: {exc}")
    t2 = time.monotonic()
    steps.start("kiug")
    try:
        soil, _ = soil_sampler(client, config, store, cfg)([site])
        sampled = replace(sampled, soil_iii=soil)
        fact = soil.get(site.site_id)
        finding = (
            f"Gleby klasy III (próbka punktów): {_pct(fact.value)}."
            if fact
            else "Brak klas gleb w próbce punktów."
        )
        steps.end("kiug", ok=True, started=t2, finding=finding)
    except Exception as exc:
        failed.append("kiug")
        steps.end("kiug", ok=False, started=t2, error=f"{type(exc).__name__}: {exc}")
    if sampled is not layers:
        scored = score_candidates([site], sampled, cfg)[0]
    score = scored.score
    steps.end(
        "scoring",
        ok=True,
        started=t0,
        finding=f"Wynik {score.score:.0f} pkt (do {score.upside_score:.0f} po wyjaśnieniu danych).",
    )

    sheet = _sheet(geom, area, neighbours_geoms, margin)
    ages: dict[str, datetime | None] = {
        "osm_power": static.osm_snapshot.fetched_at,
        "prg": static.prg_snapshot.fetched_at,
    }
    for name, layer_obj in (("isok", area.flood), ("bdot10k", area.housing)):
        if layer_obj is not None:
            ages[name] = layer_obj.as_of
    for name, layers_obj in (("midas", list((area.mining or {}).values())), ("ru", list(area.zoning))):
        if layers_obj:
            ages[name] = layers_obj[0].as_of

    return ParcelCheck(
        parcel=parcel,
        site=site,
        scored=scored,
        verdict=verdict(score),
        unknowns=unknowns(scored, cfg, failed),
        next_steps=next_steps(parcel),
        steps=list(steps.records.values()),
        sheet=sheet,
        layer_ages=ages,
        outside_aoi=parcel.in_aoi is False,
        failed_steps=failed,
    )


def _single_site(parcel: ResolvedParcel, cfg: ScoringConfig) -> Site:
    """One parcel is one site (owner, 24.10.2026). A parcel over the split size is kept whole and flagged."""
    sites = cluster_sites(
        [Parcel(parcel.parcel_id, None, parcel.geom)],
        mw_per_ha=cfg.target.mw_per_ha,
        split_suggestion_ha=cfg.target.large_site_split_ha,
    )
    return replace(sites[0], flags=[*sites[0].flags, "check"])


def _geoms(layer: Layer | None) -> list[BaseGeometry]:
    return list(layer.index.geoms) if layer is not None else []


def _sheet(
    geom: BaseGeometry, area: AreaLayers, neighbours: list[BaseGeometry], margin: float
) -> dict[str, Any]:
    zones: dict[str, list[BaseGeometry]] = {}
    for layer in area.zoning:
        for kind, zone_geom in layer.zones:
            zones.setdefault(kind.value, []).append(zone_geom)
    mining = [g for layer in (area.mining or {}).values() for g in layer.index.geoms]
    layers: list[tuple[str, str, str, Iterable[BaseGeometry]]] = [
        ("neighbours", "Sąsiednie działki", "polygon", neighbours),
        ("flood", "Strefa zalewowa 1%", "polygon", _geoms(area.flood)),
        ("flood_02", "Strefa zalewowa 0,2%", "polygon", _geoms(area.flood_02)),
        ("forest", "Las", "polygon", _geoms(area.forest)),
        ("housing", "Budynki mieszkalne", "polygon", _geoms(area.housing)),
        ("mining", "Obszar górniczy", "polygon", mining),
        *((f"zoning:{kind}", f"Plan: {kind}", "polygon", geoms) for kind, geoms in sorted(zones.items())),
    ]
    return build_sheet(geom, layers, margin_m=margin)


def load_aoi(session: Session) -> BaseGeometry | None:
    """The area of interest (EPSG:2180) from the `aoi` table, or None when it is not loaded."""
    row = session.execute(text("SELECT ST_AsEWKB(ST_Union(geom)) FROM aoi")).scalar()
    return wkb.loads(bytes(row)) if row is not None else None


def check_all(
    parcels: Sequence[ResolvedParcel], ctx: CheckContext, reporter: CheckReporter | None = None
) -> list[ParcelCheck]:
    """Check each parcel in turn. A parcel whose check cannot even start raises: the rest are not run."""
    return [check_parcel(parcel, index, ctx, reporter) for index, parcel in enumerate(parcels)]
