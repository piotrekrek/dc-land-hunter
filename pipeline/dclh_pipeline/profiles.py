"""Investor profiles ("DC budowane" / "DC kontenerowe"): a verdict lens on the site features behind the score.

Blockers come from data we have. What we have no data for is never treated as a pass: it is listed as
something to check, and when it decides the answer (zoning, area) the verdict is `manual`.
See docs/SCORING.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from dclh_pipeline.config import ProfilesConfig, ProfileSpec
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring.engine import ramp_down
from dclh_pipeline.scoring.types import (
    IN_OUZ_PARTIAL,
    PATH_MPZP_OR_ZPI,
    Fact,
    SiteFeatures,
    ZoningKind,
)

NEUTRAL = 50.0  # soft score of an input we have no data for
ROAD_ADJOINING_M = 25.0  # a public road this close runs along the plot
ROAD_FAR_M = 300.0  # beyond this a public road is no longer "near": access needs a road or an easement
HEIGHT_FLAG_M = 20.0  # a zone height limit at or below this may clash with generator stacks and cooling
ROAD_UNKNOWN = "Dostęp do drogi publicznej (EGiB, księga wieczysta)"


class Category(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    MANUAL = "manual"


@dataclass(frozen=True)
class Reason:
    key: str
    text_pl: str
    source_id: str | None = None
    as_of: datetime | None = None


@dataclass(frozen=True)
class ToCheck:
    key: str
    text_pl: str
    blocking: bool  # True: the verdict cannot be better than `manual` until this is known


@dataclass(frozen=True)
class ProfileVerdict:
    profile: str
    category: Category
    score: float  # 0-100 soft score; informative when the category is not red because of a blocker
    blockers: list[Reason] = field(default_factory=list)
    to_check: list[ToCheck] = field(default_factory=list)
    positives: list[Reason] = field(default_factory=list)


# Checks no data feed covers yet. Always shown, never blocking (docs/SCORING.md, "Profiles").
MANUAL_CHECKS = (
    ToCheck("closed_area", "Teren zamknięty lub jego strefa ochronna (MON)", False),
    ToCheck("mpzp_text", "Zapisy MPZP: zakaz obiektów kontenerowych lub tymczasowych, minima", False),
    ToCheck("title", "Tytuł prawny do nieruchomości (księga wieczysta)", False),
)  # fmt: skip


# The plan-text check names what each profile trips over; only the container profile builds temporary units.
MPZP_TEXT_PL = {
    "built": "Zapisy MPZP: ograniczenia zabudowy (wysokość, intensywność, minima)",
    "container": "Zapisy MPZP: zakaz obiektów kontenerowych lub tymczasowych, minima",
}


def _n(x: float, digits: int = 1) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def _pct(x: float) -> str:
    """A share of the site: "<1%" rather than a rounded "0%" for a small but real overlap."""
    return "<1%" if 0 < x < 1 else f"{_n(x, 0)}%"


def _ramp_up(value: float, zero: float, full: float) -> float:
    """0.0 at or below `zero`, 1.0 at or above `full`, linear in between (housing distance, headroom)."""
    return 1.0 - ramp_down(value, zero, full)


def profile_verdict(f: SiteFeatures, name: str, cfg: ProfilesConfig) -> ProfileVerdict:
    spec = cfg.profiles[name]  # type: ignore[index]
    blockers: list[Reason] = []
    to_check: list[ToCheck] = [
        replace(c, text_pl=MPZP_TEXT_PL.get(name, c.text_pl)) if c.key == "mpzp_text" else c
        for c in MANUAL_CHECKS
    ]
    positives: list[Reason] = []
    cap_yellow = False

    def reason(key: str, text: str, fact: Fact[object] | None) -> Reason:
        return Reason(key, text, fact.source_id if fact else None, fact.as_of if fact else None)

    def missing(key: str, what: str, *, blocking: bool = False) -> None:
        to_check.append(ToCheck(key, f"Brak danych: {what}", blocking))

    # --- area
    area = f.area_ha
    if area is None:
        missing("area", "powierzchnia terenu", blocking=True)
    elif area.value < spec.min_area_ha:
        blockers.append(
            reason(
                "area", f"za mała powierzchnia: {_n(area.value, 2)} ha < {_n(spec.min_area_ha, 2)} ha", area
            )
        )

    # --- shared hard filters: overlap with protected land
    hard = cfg.hard
    nature_pct = hard.protected_nature_pct_over
    overlaps = (
        ("nature", f.protected_nature_pct, nature_pct, "Natura 2000, park, rezerwat", "stref ochrony"),
        ("flood", f.flood_10_or_1_pct, hard.flood_pct_over, "strefa zalewowa 10% lub 1%", "strefy zalewowej"),
        ("forest", f.forest_pct, hard.forest_pct_over, "las", "lasów"),
        ("mining", f.mining_area_pct, hard.mining_area_pct_over, "obszar górniczy", "obszaru górniczego"),
    )  # fmt: skip
    for key, fact, limit, what, missing_what in overlaps:
        if fact is None:
            missing(key, missing_what)
            cap_yellow = True
        elif fact.value > limit:
            if key == "forest" and area is not None:
                forest_ha = area.value * fact.value / 100
                if forest_ha < hard.forest_min_ha:
                    continue
                text = f"{what}: {_n(forest_ha, 2)} ha ({_pct(fact.value)} terenu)"
                blockers.append(reason(key, text, fact))
                continue
            blockers.append(reason(key, f"{what}: {_pct(fact.value)} terenu", fact))
    heritage = f.on_heritage_register
    if heritage is None:
        # No source feeds the register (docs/SCORING.md), so the gap is on every site: listed, but it does not
        # cap the verdict, or green could never be reached.
        missing("heritage", "rejestru zabytków")
    elif hard.heritage_register and heritage.value:
        blockers.append(reason("heritage", "obiekt w rejestrze zabytków", heritage))

    # --- road access: the distance is measured (BDOT10k); the legal right to use the road never is
    road = f.dist_public_road_m
    if road is None:
        to_check.append(ToCheck("road_access", ROAD_UNKNOWN, False))
    elif road.value <= ROAD_ADJOINING_M:
        positives.append(reason("road", f"droga publiczna przy działce ({_n(road.value, 0)} m)", road))
        to_check.append(ToCheck("road_access", "Prawny dostęp do drogi: sprawdź w księdze wieczystej", False))
    elif road.value <= ROAD_FAR_M:
        to_check.append(
            ToCheck(
                "road_access",
                f"Najbliższa droga publiczna w {_n(road.value, 0)} m: dojazd może wymagać służebności "
                "(księga wieczysta)",
                False,
            )
        )
    else:
        to_check.append(
            ToCheck(
                "road_access",
                f"Najbliższa droga publiczna dopiero w {_n(road.value, 0)} m: "
                "dojazd wymaga drogi lub służebności",
                False,
            )
        )
        cap_yellow = True

    # --- zoning: unknown is not bad (the register is incomplete until 30.11.2026), but it decides the answer
    z = f.zoning.value if f.zoning else None
    if z is ZoningKind.MPZP_PRODUCTION:
        positives.append(reason("zoning", "MPZP z funkcją produkcyjną lub usługową", f.zoning))
    elif z is ZoningKind.PLAN_OGOLNY_SG:
        positives.append(reason("zoning", "plan ogólny: strefa gospodarcza", f.zoning))
        if f.in_ouz is None:  # the register has no OUZ data for this gmina
            to_check.append(
                ToCheck(
                    "ouz",
                    "Czy działka leży w obszarze uzupełnienia zabudowy (OUZ); "
                    "bez OUZ ścieżka to MPZP lub ZPI",
                    False,
                )
            )
        cap_yellow = True
    elif z is ZoningKind.OTHER:
        if spec.zoning_other == "block":
            blockers.append(
                reason("zoning", "przeznaczenie terenu nie jest produkcyjne ani usługowe", f.zoning)
            )
        else:
            to_check.append(
                ToCheck(
                    "zoning",
                    "Przeznaczenie terenu inne niż P/PU: sprawdź, czy plan dopuszcza usługi (symbol U)",
                    True,
                )
            )
    else:  # unknown, plan in procedure, or no zoning fact at all
        where = "MPZP i plan ogólny" if f.in_ouz is not None else "MPZP, plan ogólny i OUZ"
        to_check.append(
            ToCheck("zoning", f"Brak planu miejscowego w rejestrze: sprawdź {where} w gminie", True)
        )

    # --- planning path after the plan ogólny reform (facts exist only where the register has the plan)
    if f.planning_path is not None and f.planning_path.value == PATH_MPZP_OR_ZPI:
        part = " (tylko część terenu leży w OUZ)" if f.in_ouz and f.in_ouz.value == IN_OUZ_PARTIAL else ""
        to_check.append(
            ToCheck(
                "planning_path",
                f"Poza OUZ{part} i bez MPZP: w gminie z planem ogólnym decyzja WZ jest wykluczona, "
                "jedyna ścieżka to MPZP lub ZPI (12–24 miesiące)",
                False,
            )
        )
    if f.plan_ogolny_act is not None:
        to_check.append(
            ToCheck(
                "plan_validity",
                f"Sprawdź, czy uchwała {f.plan_ogolny_act.value} o planie ogólnym została opublikowana "
                "w dzienniku urzędowym województwa i nie została unieważniona",
                False,
            )
        )
    height = f.zone_max_height_m
    if height is not None and height.value <= HEIGHT_FLAG_M:
        to_check.append(
            ToCheck(
                "zone_height",
                f"Sprawdź kominy agregatów i chłodzenie względem limitu wysokości ({_n(height.value, 0)} m).",
                False,
            )
        )

    # --- soft score: input we have no data for counts 50 and is listed, never passed silently
    soft: list[tuple[float, float]] = []

    def add(weight: float, value: float | None, key: str, what: str, text: str | None) -> None:
        if value is None:
            soft.append((weight, NEUTRAL))
            missing(key, what)
            return
        soft.append((weight, 100.0 * value))
        if text and value >= 0.8:
            positives.append(Reason(key, text))

    w = spec.weights
    h = f.residential_min_m
    add(w.housing, _ramp_up(h.value, spec.housing_m.min, spec.housing_m.full) if h else None, "housing",
        "odległość od zabudowy mieszkaniowej",
        f"zabudowa mieszkaniowa dalej niż {_n(spec.housing_m.full, 0)} m" if h else None)  # fmt: skip
    g = f.dist_gpz110_km
    add(w.gpz110, ramp_down(g.value, spec.gpz110_km.full, spec.gpz110_km.zero) if g else None, "gpz110",
        "odległość do GPZ 110 kV",
        f"GPZ 110 kV w odległości {_n(g.value)} km" if g else None)  # fmt: skip
    hr = f.published_gpz_headroom_mw
    add(w.headroom, _ramp_up(hr.value, spec.headroom_mw.zero, spec.headroom_mw.full) if hr else None,
        "headroom", "opublikowana moc GPZ (szacunkowa, wiąże ekspertyza do warunków przyłączenia)",
        f"operator publikuje wolną moc przy GPZ: {_n(hr.value)} MW" if hr else None)  # fmt: skip
    s1, s3 = f.soil_class_i_ii_pct, f.soil_class_iii_pct
    soil_share = (s1.value if s1 else 0.0) + (s3.value if s3 else 0.0) if (s1 or s3) else None
    add(w.soil, None if soil_share is None else max(0.0, 1.0 - soil_share / 100.0), "soil",
        "klasy gleb", None)  # fmt: skip

    total_w = sum(wt for wt, _ in soft)
    score = sum(wt * s for wt, s in soft) / total_w if total_w else NEUTRAL

    # --- category
    if blockers:
        category = Category.RED
    elif any(c.blocking for c in to_check):
        category = Category.MANUAL
    elif score >= cfg.thresholds.green:
        category = Category.YELLOW if cap_yellow else Category.GREEN
    elif score >= cfg.thresholds.yellow:
        category = Category.YELLOW
    else:
        category = Category.RED
    return ProfileVerdict(name, category, round(score, 1), blockers, to_check, positives[:3])


def features_from_detail(stored: dict[str, Any]) -> SiteFeatures:
    """Rebuild `SiteFeatures` from the `detail.features` JSON of a stored site, so profiles can be recomputed
    without a new search."""
    built: dict[str, Fact[Any] | None] = {}
    for fld in fields(SiteFeatures):
        raw = stored.get(fld.name)
        if raw is None:
            built[fld.name] = None
            continue
        value = ZoningKind(raw["value"]) if fld.name == "zoning" else raw["value"]
        as_of = datetime.fromisoformat(raw["as_of"]) if raw.get("as_of") else None
        built[fld.name] = Fact(value, raw["source_id"], as_of, Confidence(raw["confidence"]))
    return SiteFeatures(**built)


def profile_verdicts(f: SiteFeatures, cfg: ProfilesConfig) -> dict[str, ProfileVerdict]:
    return {name: profile_verdict(f, name, cfg) for name in ("built", "container")}


__all__ = [
    "Category",
    "ProfileSpec",
    "ProfileVerdict",
    "Reason",
    "ToCheck",
    "features_from_detail",
    "profile_verdict",
    "profile_verdicts",
]
