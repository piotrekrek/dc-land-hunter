"""RTB evidence: a proposed status per workstream, from the stored site facts and the manual power evidence.

A pure function of the site's `detail` JSON (`score.items`, `score.exclusions`, `features`), the evidence
the team typed in and the thresholds in `config/scoring.yaml` (`rtb:`). It only proposes: the team decides.
Every reason names the fact and carries its source and date, so the export can link each statement to it.

Rules that hold whatever the data says:
- environment is never green with a deposit overlap, and red when a hard environmental exclusion fired;
- power is never better than yellow below `conditions_issued` (rumour, informal opinion, application filed);
- planning is green only with an MPZP in force that allows production;
- concept and commercial are the team's judgement: yellow unless data clearly rules the site out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from dclh_pipeline.config import ScoringConfig

Status = Literal["green", "yellow", "red"]

EVIDENCE_LEVELS: tuple[str, ...] = (
    "rumour",
    "informal_opinion",
    "application_filed",
    "conditions_issued",
    "agreement_signed",
)
EVIDENCE_LABEL_PL: dict[str, str] = {
    "rumour": "Plotka",
    "informal_opinion": "Nieformalna opinia operatora",
    "application_filed": "Wniosek złożony (opłata i zaliczka)",
    "conditions_issued": "Warunki wydane",
    "agreement_signed": "Umowa podpisana",
}
# From this level up the operator has put the power in writing, so power may be green.
_DOCUMENTED_FROM = EVIDENCE_LEVELS.index("conditions_issued")
POWER_EVIDENCE_SOURCE = "power_evidence"

_RANK = {"green": 0, "yellow": 1, "red": 2}
TEAM_JUDGEMENT_PL = "do oceny przez zespół"

# Hard-filter key -> feature that decided it. Only these filters decide the environment workstream.
_ENV_EXCLUSIONS: dict[str, str] = {
    "protected_nature_pct_over": "protected_nature_pct",
    "flood_10_or_1_pct": "flood_10_or_1_pct",
    "mining_area_active": "mining_area_pct",
    "forest_pct_over": "forest_pct",
    "soil_class_I_II_pct_over": "soil_class_i_ii_pct",
    "heritage_register": "on_heritage_register",
}
# Constraints that must be known before the environment can be green: (feature, name).
_ENV_REQUIRED: tuple[tuple[str, str], ...] = (
    ("protected_nature_pct", "Natura 2000 i obszary chronione"),
    ("flood_10_or_1_pct", "strefa zalewowa 10% / 1%"),
    ("forest_pct", "las"),
    ("deposit_overlap_pct", "złoża kopalin"),
    ("mining_area_pct", "obszary górnicze"),
)
# Flags that only lower the environment to yellow: (feature, name).
_ENV_FLAGS: tuple[tuple[str, str], ...] = (
    ("landscape_park_or_ochk", "park krajobrazowy lub OChK"),
    ("gas_pipeline_zone", "strefa gazociągu"),
    ("airport_ols", "powierzchnie ograniczające lotniska"),
)

_PATH_PL = {
    "mpzp_in_force": "MPZP obowiązuje",
    "mpzp_or_zpi": "potrzebny MPZP lub ZPI",
    "wz_possible": "WZ możliwa",
}
_OUZ_PL = {"yes": "w OUZ", "no": "poza OUZ", "partial": "częściowo w OUZ"}
_ZONING_PL = {
    "mpzp_production": "MPZP z przeznaczeniem produkcyjno-usługowym",
    "plan_ogolny_SG": "plan ogólny: strefa gospodarcza",
    "procedure_in_progress": "procedura planistyczna w toku",
    "unknown": "brak planu w rejestrze",
    "other": "przeznaczenie nie dopuszcza produkcji",
}


@dataclass(frozen=True)
class PowerEvidence:
    """The manual power evidence of one site (table `power_evidence`); no row means `rumour`."""

    level: str = "rumour"
    document_date: date | None = None
    conditions_expiry: date | None = None
    attachment_url: str | None = None


@dataclass(frozen=True)
class Reason:
    text_pl: str
    source_id: str | None
    as_of: str | None  # ISO date or datetime of the fact the reason rests on


@dataclass(frozen=True)
class Workstream:
    key: str
    label_pl: str
    status: Status
    reasons: tuple[Reason, ...] = field(default_factory=tuple)


def _n(value: float, digits: int = 1) -> str:
    """A number with a decimal comma and no trailing zeros: 0,5; 63; 2,4."""
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


class _Builder:
    """Collects reasons; the status is the worst one said, starting from green."""

    def __init__(self, features: Mapping[str, Any]) -> None:
        self.features = features
        self.status: Status = "green"
        self.reasons: list[Reason] = []

    def fact(self, key: str) -> Mapping[str, Any] | None:
        found = self.features.get(key)
        return found if isinstance(found, Mapping) else None

    def value(self, key: str) -> Any:
        found = self.fact(key)
        return None if found is None else found.get("value")

    def number(self, key: str) -> float | None:
        value = self.value(key)
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

    def as_of(self, key: str) -> str | None:
        found = self.fact(key)
        return None if found is None else found.get("as_of")

    def say(self, status: Status | None, text: str, key: str) -> None:
        """A reason resting on feature `key` (its source and date); `status` raises it, None only notes."""
        fact = self.fact(key)
        self._add(status, text, fact.get("source_id") if fact else None, fact.get("as_of") if fact else None)

    def say_from(
        self, status: Status | None, text: str, source: str | None, as_of: str | None = None
    ) -> None:
        """A reason with an explicit source (a missing fact, a rule, the manual evidence)."""
        self._add(status, text, source, as_of)

    def _add(self, status: Status | None, text: str, source: str | None, as_of: str | None) -> None:
        self.reasons.append(Reason(text, source, as_of))
        if status is not None and _RANK[status] > _RANK[self.status]:
            self.status = status

    def done(self, key: str, label: str) -> Workstream:
        return Workstream(key, label, self.status, tuple(self.reasons))


def _land(f: _Builder, cfg: ScoringConfig) -> Workstream:
    ha_min = _n(cfg.target.ha_min, 0)
    area = f.number("area_ha")
    if area is None:
        f.say_from("yellow", "Brak danych o powierzchni terenu.", "egib")
    elif area < cfg.target.ha_min:
        f.say("red", f"Powierzchnia {_n(area)} ha jest poniżej minimum {ha_min} ha.", "area_ha")
    else:
        f.say(None, f"Powierzchnia {_n(area)} ha (minimum {ha_min} ha).", "area_ha")
    compactness = f.number("compactness")
    if compactness is None:
        f.say_from("yellow", "Brak danych o zwartości kształtu terenu.", "egib")
    elif compactness < cfg.land.compactness_min:
        low = _n(cfg.land.compactness_min, 2)
        f.say(
            "yellow",
            f"Zwartość kształtu {_n(compactness, 2)} poniżej {low}: trudniej zabudować.",
            "compactness",
        )
    else:
        f.say(None, f"Zwartość kształtu {_n(compactness, 2)}.", "compactness")
    parcels = f.number("parcel_count")
    if parcels is None:
        f.say_from("yellow", "Brak danych o liczbie działek ewidencyjnych.", "egib")
    elif parcels > cfg.land.parcels_zero:
        text = f"{_n(parcels, 0)} działek (powyżej {cfg.land.parcels_zero}): zbyt wielu właścicieli."
        f.say("red", text, "parcel_count")
    elif parcels > cfg.rtb.parcels_yellow_over:
        text = f"{_n(parcels, 0)} działek (powyżej {cfg.rtb.parcels_yellow_over}): wielu właścicieli."
        f.say("yellow", text, "parcel_count")
    else:
        f.say(None, f"{_n(parcels, 0)} działek ewidencyjnych.", "parcel_count")
    return f.done("land", "Grunt")


def _distance_note(f: _Builder, key: str, name: str, limit_km: float, near: bool) -> None:
    km = f.number(key)
    if km is None:
        f.say_from(None, f"Brak danych: {name}.", "osm_power")
        return
    verdict = "w granicy akceptowalnej" if near else "za daleko"
    f.say(
        None, f"{name}: {_n(km)} km (akceptowalnie do {_n(limit_km, 0)} km), {verdict}; szacunek z OSM.", key
    )


def _grid_distances(f: _Builder, cfg: ScoringConfig) -> None:
    """Notes on the 110 kV distances; red when both are known and beyond the acceptable distance."""
    line, gpz = f.number("dist_line110_km"), f.number("dist_gpz110_km")
    near_line = line is not None and line <= cfg.rtb.line110_acceptable_km
    near_gpz = gpz is not None and gpz <= cfg.rtb.gpz110_acceptable_km
    _distance_note(f, "dist_line110_km", "Najbliższa linia 110 kV", cfg.rtb.line110_acceptable_km, near_line)
    _distance_note(f, "dist_gpz110_km", "Najbliższy GPZ 110/SN", cfg.rtb.gpz110_acceptable_km, near_gpz)
    if near_line or near_gpz:
        return
    when = f.as_of("dist_line110_km")
    if line is not None and gpz is not None:
        f.say_from("red", "Brak linii ani GPZ 110 kV w akceptowalnej odległości.", "osm_power", when)
    else:
        f.say_from("yellow", "Odległość do sieci 110 kV nieznana: brak danych.", "osm_power", when)


def _power(
    f: _Builder, cfg: ScoringConfig, evidence: PowerEvidence, project_mw: float | None, today: date
) -> Workstream:
    _grid_distances(f, cfg)
    label = EVIDENCE_LABEL_PL.get(evidence.level, evidence.level)
    when = evidence.document_date.isoformat() if evidence.document_date else None
    f.say_from(None, f"Dowód mocy: {label}.", POWER_EVIDENCE_SOURCE, when)
    if EVIDENCE_LEVELS.index(evidence.level) < _DOCUMENTED_FROM:
        text = "Bez wydanych warunków przyłączenia moc nie może być lepsza niż żółta."
        f.say_from("yellow", text, POWER_EVIDENCE_SOURCE, when)
    elif evidence.conditions_expiry is not None and evidence.conditions_expiry < today:
        text = f"Warunki przyłączenia wygasły {_fmt_date(evidence.conditions_expiry)}."
        f.say_from("yellow", text, POWER_EVIDENCE_SOURCE, when)
    if project_mw is not None and project_mw > cfg.target.large_campus_mw:
        node = f.number("dist_node_220_400_km")
        if node is None or node > cfg.rtb.node_220_400_km:
            km = _n(cfg.rtb.node_220_400_km, 0)
            text = f"Projekt {_n(project_mw, 0)} MW wymaga węzła 220/400 kV w promieniu {km} km."
            f.say_from("yellow", text, "osm_power", f.as_of("dist_node_220_400_km"))
    f.say_from(
        None, "Odległości do sieci to wskaźnik zastępczy, nie potwierdzona dostępność mocy.", "osm_power"
    )
    return f.done("power", "Moc")


def _planning(f: _Builder) -> Workstream:
    path, zoning, in_ouz = f.value("planning_path"), f.value("zoning"), f.value("in_ouz")
    if zoning is not None:
        f.say(None, f"Przeznaczenie: {_ZONING_PL.get(str(zoning), str(zoning))}.", "zoning")
    if path is not None:
        ouz = _OUZ_PL.get(str(in_ouz), "OUZ nieznany")
        f.say(None, f"Ścieżka planistyczna: {_PATH_PL.get(str(path), str(path))} ({ouz}).", "planning_path")
    if zoning == "other":
        f.say("red", "Przeznaczenie terenu wyklucza zabudowę produkcyjną.", "zoning")
    elif path == "mpzp_in_force" and zoning == "mpzp_production":
        f.say("green", "MPZP obowiązuje i dopuszcza produkcję.", "planning_path")
    elif zoning is None and path is None:
        text = (
            "Brak danych planistycznych: brak planu w Rejestrze Urbanistycznym oznacza „nieznane”, nie „złe”."
        )
        f.say_from("yellow", text, "ru")
    else:
        text = "Brak obowiązującego MPZP z przeznaczeniem produkcyjnym: potrzebny plan, ZPI albo decyzja WZ."
        f.say("yellow", text, "planning_path" if path is not None else "zoning")
    return f.done("planning", "Planowanie")


def _deposit_names(f: _Builder) -> str:
    listed = f.value("deposit_list")
    names: list[str] = []
    if isinstance(listed, Sequence) and not isinstance(listed, str):
        for d in listed:
            if isinstance(d, Mapping) and d.get("name"):
                mineral = str(d.get("mineral") or "").lower()
                names.append(f"{d['name']} ({mineral})" if mineral else str(d["name"]))
    return ", ".join(names)


def _environment(f: _Builder, cfg: ScoringConfig, exclusions: Sequence[Mapping[str, Any]]) -> Workstream:
    for ex in exclusions:
        feature = _ENV_EXCLUSIONS.get(str(ex.get("key")))
        if feature is not None:
            f.say("red" if ex.get("mode") == "exclude" else "yellow", str(ex.get("reason_pl")), feature)
    overlap = f.number("deposit_overlap_pct")
    if overlap is not None and overlap > 0:
        names = _deposit_names(f)
        what = f"Złoże „{names}”" if names else "Udokumentowane złoże kopaliny"
        text = f"{what} pod {_n(overlap, 0)}% terenu: środowisko nie może być zielone (MIDAS)."
        f.say("yellow", text, "deposit_overlap_pct")
    over: tuple[tuple[str, float, str], ...] = (
        ("flood_0_2_pct", 0.0, "Strefa zalewowa 0,2%"),
        ("mining_terrain_pct", 0.0, "Teren górniczy"),
        ("forest_pct", cfg.rtb.forest_yellow_pct, "Las"),
        ("soil_class_iii_pct", cfg.rtb.soil_iii_yellow_pct, "Gleby klasy III"),
    )
    for key, limit, name in over:
        pct = f.number(key)
        if pct is not None and pct > limit:
            f.say("yellow", f"{name}: {_n(pct, 0)}% terenu (próg {_n(limit, 0)}%).", key)
    for key, name in _ENV_FLAGS:
        if f.value(key) is True:
            f.say("yellow", f"Teren w obszarze: {name}.", key)
    for key, name in _ENV_REQUIRED:
        number = f.number(key)
        if number is None:
            f.say_from("yellow", f"Brak danych: {name}.", None)
        elif number == 0:
            f.say(None, f"{name.capitalize()}: brak nakładania.", key)
    if f.value("env_decision_found") is None:
        f.say_from(None, "Decyzja środowiskowa: brak danych, do sprawdzenia w urzędzie.", None)
    return f.done("environment", "Środowisko")


def _concept(
    f: _Builder, cfg: ScoringConfig, exclusions: Sequence[Mapping[str, Any]], split: bool
) -> Workstream:
    area = f.number("area_ha")
    for ex in exclusions:
        if ex.get("key") == "residential_within_m" and ex.get("mode") == "exclude":
            f.say("red", str(ex.get("reason_pl")), "residential_min_m")
    near = f.number("residential_min_m")
    if near is not None and near < cfg.rtb.residential_yellow_m:
        text = f"Najbliższa zabudowa mieszkaniowa: {_n(near, 0)} m (BDOT10k): ryzyko sprzeciwu mieszkańców."
        f.say(None, text, "residential_min_m")
    if area is not None and area < cfg.target.ha_min:
        text = f"Teren {_n(area)} ha nie mieści koncepcji (minimum {_n(cfg.target.ha_min, 0)} ha)."
        f.say("red", text, "area_ha")
    elif split and area is not None:
        lo, hi = cfg.target.ha_ideal
        f.say(None, f"Teren {_n(area)} ha: rozważ podział na działkę {_n(lo, 0)}–{_n(hi, 0)} ha.", "area_ha")
    f.say_from("yellow", f"Układ, chłodzenie i odzysk ciepła: {TEAM_JUDGEMENT_PL}.", None)
    return f.done("concept", "Koncepcja")


def _commercial(f: _Builder) -> Workstream:
    if f.value("public_seller") is True:
        f.say(None, "Sprzedający jest podmiotem publicznym.", "public_seller")
    f.say_from("yellow", f"Cena, sprzedający i model transakcji: {TEAM_JUDGEMENT_PL}.", None)
    return f.done("commercial", "Handel")


def propose_rag(
    detail: Mapping[str, Any],
    evidence: PowerEvidence,
    cfg: ScoringConfig,
    *,
    today: date | None = None,
) -> list[Workstream]:
    """Six workstreams with a proposed status and reasons, from a site's `detail` JSON."""
    if evidence.level not in EVIDENCE_LEVELS:
        raise ValueError(f"unknown power evidence level: {evidence.level!r}")
    score = detail.get("score") or {}
    features = detail.get("features") or {}
    exclusions: Sequence[Mapping[str, Any]] = score.get("exclusions") or []
    project_mw = score.get("project_mw")
    day = today or datetime.now(UTC).date()

    def new() -> _Builder:
        return _Builder(features)

    return [
        _land(new(), cfg),
        _power(new(), cfg, evidence, float(project_mw) if project_mw is not None else None, day),
        _planning(new()),
        _environment(new(), cfg, exclusions),
        _concept(new(), cfg, exclusions, bool(score.get("split_suggested"))),
        _commercial(new()),
    ]
