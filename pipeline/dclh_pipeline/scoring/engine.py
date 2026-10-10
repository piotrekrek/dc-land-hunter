"""Site scoring (brief section 6). A pure function of `SiteFeatures` and `ScoringConfig`.

Every category produces points on a 0..100 scale that is then multiplied by its weight, so the weights decide how
much each category matters and the whole score stays on 0..100. Within a category the sub-criteria have fixed
maxima that sum to 100 (grid sums to 118 and is capped, see docs/SCORING.md). The bonus category is expressed in
final-score points and capped at its weight. Penalties are subtracted afterwards.

Missing data scores 0, is shown as "?" and lowers `completeness`; `upside_score` is what the site would score if
every missing criterion turned out best-case.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from dclh_pipeline.config import ScoringConfig
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.scoring.types import (
    CategoryScore,
    Exclusion,
    Fact,
    GridBadge,
    ScoreItem,
    SiteFeatures,
    SiteScore,
    ZoningKind,
)

CATEGORIES = ("grid", "zoning", "land", "connectivity", "bonus")

# Fixed sub-criterion maxima (raw points inside a 0..100 category). Documented in docs/SCORING.md.
GRID_PROXIMITY_MAX = 60.0
GRID_N1_MAX = 10.0
GRID_NODE_MAX = 10.0
GRID_NODE_MAX_LARGE = 20.0
GRID_PLANNED_STATION_MAX = 5.0
GRID_ODB_MAX = 3.0
LAND_SIZE_MAX = 35.0
LAND_COMPACT_MAX = 25.0
LAND_PARCELS_MAX = 30.0
LAND_EXPANSION_MAX = 10.0
CONN_TELECOM_MAX = 50.0
CONN_CITY_MAX = 50.0
SOIL_III_PENALTY_THRESHOLD_PCT = 30.0
RESIDENTIAL_PENALTY_BAND_M = (100.0, 300.0)


def ramp_down(value: float, full: float, zero: float) -> float:
    """1.0 at or below `full`, 0.0 at or above `zero`, linear in between (for distances: closer is better)."""
    if value <= full:
        return 1.0
    if value >= zero:
        return 0.0
    return (zero - value) / (zero - full)


def _n(x: float, digits: int = 1) -> str:
    """Polish decimal formatting for explanations."""
    return f"{x:.{digits}f}".replace(".", ",")


@dataclass
class _Draft:
    """An item before category scaling."""

    key: str
    category: str
    label_pl: str
    raw: float
    max_raw: float
    upside_raw: float
    confidence: Confidence
    source_id: str | None
    as_of: datetime | None
    explanation_pl: str
    value: float | str | bool | None = None


def _missing(
    key: str, category: str, label: str, max_raw: float, what: str, *, upside: float | None = None
) -> _Draft:
    return _Draft(
        key,
        category,
        label,
        0.0,
        max_raw,
        max_raw if upside is None else upside,
        Confidence.MISSING,
        None,
        None,
        f"Brak danych: {what}. Liczone jako 0 pkt.",
    )


def _scored[T](
    key: str,
    category: str,
    label: str,
    fact: Fact[T] | None,
    max_raw: float,
    fn: Callable[[T], tuple[float, str]],
    what: str,
    *,
    missing_upside: float | None = None,
) -> _Draft:
    if fact is None:
        return _missing(key, category, label, max_raw, what, upside=missing_upside)
    raw, why = fn(fact.value)
    value = fact.value if isinstance(fact.value, (int, float, str, bool)) else str(fact.value)
    return _Draft(
        key, category, label, raw, max_raw, raw, fact.confidence, fact.source_id, fact.as_of, why, value
    )


# --------------------------------------------------------------------------- categories


def _grid_items(f: SiteFeatures, cfg: ScoringConfig, est_mw: float | None) -> list[_Draft]:
    g = cfg.grid
    large = est_mw is not None and est_mw > cfg.target.large_campus_mw
    items: list[_Draft] = []

    line, gpz = f.dist_line110_km, f.dist_gpz110_km
    if line is None and gpz is None:
        items.append(
            _missing(
                "grid_proximity",
                "grid",
                "Bliskość sieci 110 kV",
                GRID_PROXIMITY_MAX,
                "odległość do linii i GPZ 110 kV",
            )
        )
    else:
        line_s = ramp_down(line.value, g.line110_km_full, g.line110_km_zero) if line else 0.0
        gpz_s = ramp_down(gpz.value, g.gpz110_km_full, g.gpz110_km_zero) if gpz else 0.0
        best = max(line_s, gpz_s)
        used = line if line_s >= gpz_s and line else (gpz or line)
        assert used is not None
        parts = []
        if line:
            parts.append(
                f"linia 110 kV: {_n(line.value)} km (pełne punkty do {_n(g.line110_km_full)} km, zero od {_n(g.line110_km_zero)} km)"
            )
        if gpz:
            parts.append(
                f"GPZ 110 kV: {_n(gpz.value)} km (pełne do {_n(g.gpz110_km_full)} km, zero od {_n(g.gpz110_km_zero)} km)"
            )
        items.append(
            _Draft(
                "grid_proximity",
                "grid",
                "Bliskość sieci 110 kV",
                best * GRID_PROXIMITY_MAX,
                GRID_PROXIMITY_MAX,
                best * GRID_PROXIMITY_MAX,
                used.confidence,
                used.source_id,
                used.as_of,
                "Liczy się lepszy z dwóch wskaźników: "
                + "; ".join(parts)
                + ". To przybliżenie, nie gwarancja mocy.",
                used.value,
            )
        )

    items.append(
        _scored(
            "grid_n1",
            "grid",
            "Dwa niezależne zasilania 110 kV",
            f.independent_110_lines_near,
            GRID_N1_MAX,
            lambda n: (
                (
                    GRID_N1_MAX,
                    f"{n} niezależne linie 110 kV w promieniu {_n(g.two_independent_110_lines_within_km, 0)} km (rezerwowanie N-1).",
                )
                if n >= 2
                else (
                    0.0,
                    f"Tylko {n} linia 110 kV w promieniu {_n(g.two_independent_110_lines_within_km, 0)} km – brak rezerwy N-1.",
                )
            ),
            "liczba linii 110 kV w pobliżu",
        )
    )

    node_max = GRID_NODE_MAX_LARGE if large else GRID_NODE_MAX
    items.append(
        _scored(
            "grid_node",
            "grid",
            "Węzeł 220/400 kV PSE w pobliżu",
            f.dist_node_220_400_km,
            node_max,
            lambda d: (
                (
                    node_max,
                    f"Węzeł 220/400 kV w odległości {_n(d)} km (≤ {_n(g.node_220_400_km_bonus, 0)} km)."
                    + (" Duży kampus – to ważny atut." if large else ""),
                )
                if d <= g.node_220_400_km_bonus
                else (
                    0.0,
                    f"Najbliższy węzeł 220/400 kV: {_n(d)} km (> {_n(g.node_220_400_km_bonus, 0)} km)."
                    + (" Dla dużego kampusu to poważna słabość." if large else ""),
                )
            ),
            "odległość do węzła 220/400 kV",
        )
    )

    items.append(
        _scored(
            "grid_planned_station",
            "grid",
            "Planowana stacja odbiorcza PSE",
            f.dist_pse_planned_station_km,
            GRID_PLANNED_STATION_MAX,
            lambda d: (
                (GRID_PLANNED_STATION_MAX, f"Planowana stacja odbiorcza PSE w odległości {_n(d)} km.")
                if d <= g.planned_pse_load_station_km_bonus
                else (
                    0.0,
                    f"Najbliższa planowana stacja PSE: {_n(d)} km (> {_n(g.planned_pse_load_station_km_bonus, 0)} km).",
                )
            ),
            "planowane stacje odbiorcze PSE",
        )
    )

    items.append(
        _scored(
            "grid_headroom",
            "grid",
            "Opublikowana moc w pobliskim GPZ",
            f.published_gpz_headroom_mw,
            g.published_gpz_headroom_bonus,
            lambda mw: (
                (
                    g.published_gpz_headroom_bonus,
                    f"OSD publikuje {_n(mw)} MW dostępnej mocy odbiorczej (SN) w GPZ. To sygnał rezerwy, nie odpowiedź dla 60 MW.",
                )
                if mw > 0
                else (0.0, "OSD publikuje brak wolnej mocy odbiorczej w pobliskim GPZ.")
            ),
            "opublikowana moc OSD dla pobliskiego GPZ",
        )
    )

    items.append(
        _scored(
            "grid_odb",
            "grid",
            "Wpisy ODB PSE w pobliżu (popyt)",
            f.dist_pse_odb_km,
            GRID_ODB_MAX,
            lambda d: (
                (
                    GRID_ODB_MAX,
                    f"Wydane warunki PSE dla odbiorców w odległości {_n(d)} km – sygnał popytu (i konkurencji).",
                )
                if d <= g.pse_odb_nearby_km
                else (
                    0.0,
                    f"Brak wpisów ODB w promieniu {_n(g.pse_odb_nearby_km, 0)} km (najbliższy: {_n(d)} km).",
                )
            ),
            "wykaz ODB PSE",
        )
    )

    items.append(
        _scored(
            "grid_brownfield",
            "grid",
            "Teren poprzemysłowy z własną stacją",
            f.powered_brownfield,
            g.powered_brownfield_bonus,
            lambda yes: (
                (
                    g.powered_brownfield_bonus,
                    "Teren z własną stacją (nie OSD/PSE): istniejące przyłącze może nadawać się do ponownego użycia.",
                )
                if yes
                else (0.0, "Nie wykryto własnej stacji na terenie.")
            ),
            "wykrywanie stacji na terenach przemysłowych",
        )
    )

    items.append(
        _scored(
            "grid_closed",
            "grid",
            "Zamknięta rozdzielnia PSE w pobliżu",
            f.closed_switchyard_nearby,
            0.0,
            lambda yes: (
                (
                    -g.closed_switchyard_penalty,
                    "W pobliżu jest rozdzielnia PSE bez możliwości przyłączeń (lista zamkniętych).",
                )
                if yes
                else (0.0, "Brak zamkniętych rozdzielni PSE w pobliżu.")
            ),
            "lista zamkniętych rozdzielni PSE",
            missing_upside=0.0,
        )
    )
    return items


_ZONING_LABEL = {
    ZoningKind.MPZP_PRODUCTION: "MPZP z przeznaczeniem produkcyjno-usługowym",
    ZoningKind.PLAN_OGOLNY_SG: "plan ogólny: strefa gospodarcza (SG)",
    ZoningKind.PROCEDURE_IN_PROGRESS: "procedura planistyczna w toku",
    ZoningKind.UNKNOWN: "brak planu w rejestrze (nieznane, nie złe)",
    ZoningKind.OTHER: "inne przeznaczenie",
}


def _zoning_items(f: SiteFeatures, cfg: ScoringConfig) -> list[_Draft]:
    z = cfg.zoning
    table = {
        ZoningKind.MPZP_PRODUCTION: z.mpzp_production,
        ZoningKind.PLAN_OGOLNY_SG: z.plan_ogolny_SG,
        ZoningKind.PROCEDURE_IN_PROGRESS: z.procedure_in_progress,
        ZoningKind.UNKNOWN: z.unknown,
        ZoningKind.OTHER: z.other,
    }
    if f.zoning is None:
        # Per the config an unknown zoning is worth `zoning.unknown` points (the register is incomplete), but it is
        # still a gap in our knowledge, so it counts as missing for completeness and the upside assumes MPZP.
        return [
            _Draft(
                "zoning", "zoning", "Przeznaczenie terenu", table[ZoningKind.UNKNOWN], 100.0, table[ZoningKind.MPZP_PRODUCTION],
                Confidence.MISSING, None, None,
                f"Brak danych o planie. Brak planu oznacza „nieznane”, nie „złe”: {_n(table[ZoningKind.UNKNOWN], 0)}% maksymalnej punktacji za przeznaczenie. "
                "Zapytaj gminę o MPZP / plan ogólny.",
                ZoningKind.UNKNOWN.value,
            )
        ]  # fmt: skip
    kind = f.zoning.value
    pts = table[kind]
    conf = Confidence.MISSING if kind is ZoningKind.UNKNOWN else f.zoning.confidence
    return [
        _Draft(
            "zoning", "zoning", "Przeznaczenie terenu", pts, 100.0, table[ZoningKind.MPZP_PRODUCTION] if conf is Confidence.MISSING else pts,
            conf, f.zoning.source_id, f.zoning.as_of, f"{_ZONING_LABEL[kind].capitalize()}: {_n(pts, 0)}% maksymalnej punktacji za przeznaczenie.", kind.value,
        )
    ]  # fmt: skip


def _land_items(f: SiteFeatures, cfg: ScoringConfig) -> list[_Draft]:
    lo, hi = cfg.target.ha_ideal
    split = cfg.target.large_site_split_ha
    land = cfg.land

    def size(ha: float) -> tuple[float, str]:
        if ha < cfg.target.ha_min:
            return 0.0, f"{_n(ha)} ha to mniej niż minimum {_n(cfg.target.ha_min, 0)} ha."
        if lo <= ha <= hi:
            return LAND_SIZE_MAX, f"{_n(ha)} ha mieści się w idealnym przedziale {_n(lo, 0)}–{_n(hi, 0)} ha."
        if ha <= split:
            frac = ramp_down(ha, hi, split)
            return (
                25.0 + 10.0 * frac,
                f"{_n(ha)} ha to więcej niż idealne {_n(hi, 0)} ha, ale nadal do zagospodarowania.",
            )
        return (
            20.0,
            f"{_n(ha)} ha to duży teren (> {_n(split, 0)} ha): do podziału, sugerowana działka 10–30 ha przy sieci.",
        )

    def compact(c: float) -> tuple[float, str]:
        pts = (
            0.0
            if c <= land.compactness_min
            else LAND_COMPACT_MAX * (c - land.compactness_min) / (1 - land.compactness_min)
        )
        return (
            pts,
            f"Zwartość kształtu {_n(c, 2)} (minimum {_n(land.compactness_min, 2)}); im bliżej 1, tym łatwiej zabudować.",
        )

    def parcels(n: int) -> tuple[float, str]:
        pts = LAND_PARCELS_MAX * ramp_down(n, land.parcels_full, land.parcels_zero)
        return (
            pts,
            f"{n} działek ewidencyjnych (pełne punkty do {land.parcels_full}, zero od {land.parcels_zero}); mniej działek to zwykle mniej właścicieli.",
        )

    def expansion(ha: float) -> tuple[float, str]:
        pts = min(LAND_EXPANSION_MAX, ha / 10.0 * land.expansion_ha_bonus_per_10ha)
        return pts, f"{_n(ha)} ha dodatkowego terenu o zgodnym przeznaczeniu obok (rezerwa na rozbudowę)."

    return [
        _scored("land_size", "land", "Wielkość terenu", f.area_ha, LAND_SIZE_MAX, size, "powierzchnia terenu"),
        _scored("land_compactness", "land", "Kształt (zwartość)", f.compactness, LAND_COMPACT_MAX, compact, "geometria terenu"),
        _scored("land_parcels", "land", "Liczba działek", f.parcel_count, LAND_PARCELS_MAX, parcels, "liczba działek ewidencyjnych"),
        _scored("land_expansion", "land", "Rezerwa na rozbudowę", f.expansion_ha, LAND_EXPANSION_MAX, expansion, "teren do rozbudowy"),
    ]  # fmt: skip


def _connectivity_items(f: SiteFeatures, cfg: ScoringConfig) -> list[_Draft]:
    c = cfg.connectivity
    return [
        _scored(
            "conn_telecom", "connectivity", "Węzeł telekomunikacyjny w pobliżu", f.dist_telecom_node_km, CONN_TELECOM_MAX,
            lambda d: (
                CONN_TELECOM_MAX * ramp_down(d, c.telecom_node_km_full, c.telecom_node_km_zero),
                f"Najbliższy węzeł telekomunikacyjny: {_n(d)} km (pełne punkty do {_n(c.telecom_node_km_full, 0)} km, zero od {_n(c.telecom_node_km_zero, 0)} km). To przybliżenie dostępności światłowodu.",
            ),
            "węzły telekomunikacyjne (UKE)",
        ),
        _scored(
            "conn_city", "connectivity", "Odległość do dużego miasta", f.dist_big_city_km, CONN_CITY_MAX,
            lambda d: (
                CONN_CITY_MAX * ramp_down(d, c.city_km_full, c.city_km_zero),
                f"Najbliższe duże miasto: {_n(d, 0)} km (pełne punkty do {_n(c.city_km_full, 0)} km, zero od {_n(c.city_km_zero, 0)} km).",
            ),
            "odległość do dużych miast",
        ),
    ]  # fmt: skip


def _bonus_items(f: SiteFeatures, cfg: ScoringConfig) -> list[_Draft]:
    b = cfg.bonus
    return [
        _scored(
            "bonus_heat", "bonus", "Sieć ciepłownicza w pobliżu", f.dist_heat_network_km, b.heat_network_pts,
            lambda d: (
                (b.heat_network_pts, f"Sieć ciepłownicza w odległości {_n(d)} km: możliwy odzysk ciepła (EED art. 26).")
                if d <= b.heat_network_km
                else (0.0, f"Najbliższa sieć ciepłownicza: {_n(d)} km (> {_n(b.heat_network_km, 0)} km).")
            ),
            "sieci ciepłownicze",
        ),
        _scored(
            "bonus_psi", "bonus", "Pomoc regionalna (PSI)", f.psi_aid_pts, b.psi_aid_max_pts,
            lambda pts: (max(0.0, min(pts, b.psi_aid_max_pts)), f"Intensywność pomocy w gminie: {_n(pts)} z {_n(b.psi_aid_max_pts, 0)} pkt."),
            "mapa pomocy regionalnej",
        ),
        _scored(
            "bonus_res", "bonus", "Źródła OZE w pobliżu", f.dist_res_cluster_km, b.res_cluster_pts,
            lambda d: (
                (b.res_cluster_pts, f"Skupisko OZE w odległości {_n(d)} km.")
                if d <= b.res_cluster_km
                else (0.0, f"Najbliższe skupisko OZE: {_n(d)} km (> {_n(b.res_cluster_km, 0)} km).")
            ),
            "generatory OZE",
        ),
        _scored(
            "bonus_env", "bonus", "Decyzja środowiskowa", f.env_decision_found, b.env_decision_found,
            lambda yes: ((b.env_decision_found, "Znaleziono decyzję środowiskową dla terenu.") if yes else (0.0, "Nie znaleziono decyzji środowiskowej.")),
            "decyzje środowiskowe",
        ),
        _scored(
            "bonus_seller", "bonus", "Sprzedający publiczny", f.public_seller, b.public_seller,
            lambda yes: ((b.public_seller, "Sprzedający to podmiot publiczny (prostsza transakcja).") if yes else (0.0, "Sprzedający nie jest podmiotem publicznym.")),
            "informacja o sprzedającym",
        ),
    ]  # fmt: skip


def _penalty_items(f: SiteFeatures, cfg: ScoringConfig) -> list[_Draft]:
    p = cfg.penalties

    def flag(
        key: str, label: str, fact: Fact[bool] | None, pts: float, yes: str, no: str, what: str
    ) -> _Draft:
        return _scored(
            key,
            "penalty",
            label,
            fact,
            0.0,
            lambda v: (-pts, yes) if v else (0.0, no),
            what,
            missing_upside=0.0,
        )

    def share(
        key: str,
        label: str,
        fact: Fact[float] | None,
        pts: float,
        threshold: float,
        yes: str,
        no: str,
        what: str,
    ) -> _Draft:
        return _scored(
            key,
            "penalty",
            label,
            fact,
            0.0,
            lambda v: (-pts, yes.format(v=_n(v, 0))) if v > threshold else (0.0, no),
            what,
            missing_upside=0.0,
        )

    lo, hi = RESIDENTIAL_PENALTY_BAND_M
    return [
        share("pen_flood_02", "Strefa zalewowa 0,2%", f.flood_0_2_pct, p.flood_0_2pct, 0.0, "{v}% terenu w strefie zagrożenia powodziowego 0,2%.", "Teren poza strefą 0,2%.", "mapy zagrożenia powodziowego"),
        flag("pen_landscape", "Park krajobrazowy / OChK", f.landscape_park_or_ochk, p.landscape_park_or_ochk, "Teren w parku krajobrazowym lub obszarze chronionego krajobrazu.", "Poza parkami krajobrazowymi i OChK.", "formy ochrony przyrody"),
        share("pen_mining_terrain", "Teren górniczy", f.mining_terrain_pct, p.mining_terrain, 0.0, "{v}% terenu w terenie górniczym.", "Poza terenami górniczymi.", "tereny górnicze"),
        share("pen_soil_iii", "Gleby klasy III", f.soil_class_iii_pct, p.soil_class_III_pct_over_30, SOIL_III_PENALTY_THRESHOLD_PCT, "{v}% terenu to gleby klasy III (próg 30%).", "Mało gleb klasy III.", "klasy gleb"),
        _scored(
            "pen_residential", "penalty", "Zabudowa mieszkaniowa 100–300 m", f.residential_min_m, 0.0,
            lambda m: (
                (-p.residential_100_300m, f"Zabudowa mieszkaniowa w odległości {_n(m, 0)} m (strefa {_n(lo, 0)}–{_n(hi, 0)} m): ryzyko sprzeciwu mieszkańców.")
                if lo <= m < hi
                else (0.0, f"Najbliższa zabudowa mieszkaniowa: {_n(m, 0)} m.")
            ),
            "zabudowa mieszkaniowa", missing_upside=0.0,
        ),
        flag("pen_gas", "Strefa gazociągu", f.gas_pipeline_zone, p.gas_pipeline_zone, "Teren w strefie kontrolowanej gazociągu.", "Poza strefami gazociągów.", "gazociągi"),
        flag("pen_airport", "Obszar ograniczeń lotniska (OLS)", f.airport_ols, p.airport_ols, "Teren w obszarze ograniczeń wysokości zabudowy lotniska.", "Poza OLS lotnisk.", "obszary ograniczeń lotnisk"),
    ]  # fmt: skip


# --------------------------------------------------------------------------- hard filters


def evaluate_hard_filters(f: SiteFeatures, cfg: ScoringConfig) -> tuple[list[Exclusion], list[bool]]:
    """Returns triggered filters and, per filter, whether its input data was available (for completeness)."""
    hf = cfg.hard_filters
    triggered: list[Exclusion] = []
    known: list[bool] = []

    def record(key: str, available: bool, reason: str | None) -> None:
        known.append(available)
        if reason:
            spec = hf[key]
            triggered.append(Exclusion(key, reason, spec.mode, spec.penalty_points))

    def over(key: str, fact: Fact[float] | None, what: str) -> None:
        threshold = hf[key].threshold_pct or 0.0
        reason = None
        if fact is not None and fact.value > threshold:
            reason = f"{what}: {_n(fact.value, 0)}% terenu (próg {_n(threshold, 0)}%)"
        record(key, fact is not None, reason)

    ha_min = cfg.target.ha_min
    area = f.area_ha
    record(
        "area_below_min",
        area is not None,
        f"za mała powierzchnia ({_n(area.value)} ha < {_n(ha_min, 0)} ha)"
        if area and area.value < ha_min
        else None,
    )
    over("protected_nature_pct_over", f.protected_nature_pct, "Natura 2000, park narodowy lub rezerwat")
    over("flood_10_or_1_pct", f.flood_10_or_1_pct, "strefa zalewowa 10% lub 1%")
    over("mining_area_active", f.mining_area_pct, "obszar górniczy")
    over("forest_pct_over", f.forest_pct, "las")
    over("soil_class_I_II_pct_over", f.soil_class_i_ii_pct, "gleby klas I–II")
    heritage = f.on_heritage_register
    record(
        "heritage_register",
        heritage is not None,
        "obiekt w rejestrze zabytków" if heritage and heritage.value else None,
    )
    min_m = hf["residential_within_m"].threshold_m or 100.0
    res = f.residential_min_m
    record(
        "residential_within_m",
        res is not None,
        f"zabudowa mieszkaniowa w odległości {_n(res.value, 0)} m (< {_n(min_m, 0)} m)"
        if res and res.value < min_m
        else None,
    )
    return triggered, known


# --------------------------------------------------------------------------- scoring


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_site(f: SiteFeatures, cfg: ScoringConfig) -> SiteScore:
    est_mw = f.area_ha.value * cfg.target.mw_per_ha if f.area_ha else None
    large = est_mw is not None and est_mw > cfg.target.large_campus_mw
    split = f.area_ha is not None and f.area_ha.value > cfg.target.large_site_split_ha

    drafts: dict[str, list[_Draft]] = {
        "grid": _grid_items(f, cfg, est_mw),
        "zoning": _zoning_items(f, cfg),
        "land": _land_items(f, cfg),
        "connectivity": _connectivity_items(f, cfg),
        "bonus": _bonus_items(f, cfg),
    }
    weights = cfg.weights.model_dump()

    items: list[ScoreItem] = []
    categories: list[CategoryScore] = []
    total = upside_total = 0.0
    for cat in CATEGORIES:
        cat_items = drafts[cat]
        # The bonus category is already in final-score points and capped at its weight; the others are 0..100.
        cap = weights[cat] if cat == "bonus" else 100.0
        base_scale = 1.0 if cat == "bonus" else weights[cat] / 100.0

        def scale(raws: list[float], *, cap: float = cap, base_scale: float = base_scale) -> float:
            positive = sum(r for r in raws if r > 0)
            negative = sum(r for r in raws if r < 0)
            net = _clip(positive + negative, 0.0, cap)
            # When the positives overflow the cap, shrink them proportionally so displayed items add up.
            shrink = (cap - negative) / positive if positive > 0 and positive + negative > cap else 1.0
            return base_scale * min(1.0, shrink) if net > 0 else base_scale

        s_act = scale([d.raw for d in cat_items])
        s_up = scale([d.upside_raw for d in cat_items])
        cat_pts = _clip(sum(d.raw for d in cat_items), 0.0, cap) * base_scale
        cat_up = _clip(sum(d.upside_raw for d in cat_items), 0.0, cap) * base_scale
        cat_max = cap * base_scale
        categories.append(CategoryScore(cat, cat_pts, cat_max, cat_up))
        total += cat_pts
        upside_total += cat_up
        for d in cat_items:
            items.append(
                ScoreItem(
                    d.key,
                    d.category,
                    d.label_pl,
                    d.raw * s_act,
                    d.max_raw * base_scale,
                    d.upside_raw * s_up,
                    d.confidence,
                    d.source_id,
                    d.as_of,
                    d.explanation_pl,
                    d.value,
                )
            )

    penalties = _penalty_items(f, cfg)
    pen_total = sum(d.raw for d in penalties)  # negative or zero
    items.extend(
        ScoreItem(
            d.key,
            d.category,
            d.label_pl,
            d.raw,
            0.0,
            d.upside_raw,
            d.confidence,
            d.source_id,
            d.as_of,
            d.explanation_pl,
            d.value,
        )
        for d in penalties
    )

    exclusions, hf_known = evaluate_hard_filters(f, cfg)
    soft = sum(e.penalty_points for e in exclusions if e.mode == "penalty")
    excluded = any(e.mode == "exclude" for e in exclusions)

    score = _clip(total + pen_total - soft, 0.0, 100.0)
    upside = _clip(upside_total + sum(d.upside_raw for d in penalties) - soft, 0.0, 100.0)

    known_flags = [it.confidence is not Confidence.MISSING for it in items] + hf_known
    completeness = sum(known_flags) / len(known_flags) if known_flags else 0.0

    if f.published_gpz_headroom_mw is not None:
        badge = GridBadge.PUBLISHED
    elif any(x is not None for x in (f.dist_line110_km, f.dist_gpz110_km, f.dist_node_220_400_km)):
        badge = GridBadge.PROXY
    else:
        badge = GridBadge.NONE

    flags: list[str] = []
    if large:
        flags.append(
            f"Duży kampus (szac. {_n(est_mw or 0, 0)} MW > {_n(cfg.target.large_campus_mw, 0)} MW): potrzebny węzeł 220/400 kV."
        )
    if split:
        flags.append("Duży teren, do podziału: sugerowana działka 10–30 ha najbliżej sieci.")
    if est_mw is not None and est_mw < cfg.target.mw_min:
        flags.append(f"Szacowana moc {_n(est_mw, 0)} MW jest poniżej minimum {_n(cfg.target.mw_min, 0)} MW.")

    return SiteScore(
        score=score,
        upside_score=max(upside, score),
        completeness=completeness,
        excluded=excluded,
        exclusions=exclusions,
        categories=categories,
        items=items,
        estimated_mw=est_mw,
        large_campus=large,
        split_suggested=split,
        grid_badge=badge,
        flags_pl=flags,
    )
