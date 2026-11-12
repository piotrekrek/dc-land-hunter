"""RTB evidence: the red-flag list and the facts table, both pure functions of a site's `detail` JSON.

Every row carries its source, date and confidence, so a partner can trace each number in the export.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# Hard-filter key -> feature that decided it; the flag takes that feature's source and date.
_FILTER_FEATURE: dict[str, str] = {
    "area_below_min": "area_ha",
    "protected_nature_pct_over": "protected_nature_pct",
    "flood_10_or_1_pct": "flood_10_or_1_pct",
    "mining_area_active": "mining_area_pct",
    "forest_pct_over": "forest_pct",
    "soil_class_I_II_pct_over": "soil_class_i_ii_pct",
    "heritage_register": "on_heritage_register",
    "residential_within_m": "residential_min_m",
}


@dataclass(frozen=True)
class RedFlag:
    kind: Literal["exclusion", "penalty"]  # a hard filter that excludes, or points taken off
    key: str
    label_pl: str
    reason_pl: str
    points: float  # points taken off (positive); 0 for an exclusion, which removes the site
    source_id: str | None
    as_of: str | None
    confidence: str | None


def red_flags(detail: Mapping[str, Any]) -> list[RedFlag]:
    """Fired hard filters and penalties of the stored score: what a partner must see before anything else."""
    score = detail.get("score") or {}
    features = detail.get("features") or {}
    flags: list[RedFlag] = []
    for ex in score.get("exclusions") or []:
        fact = features.get(_FILTER_FEATURE.get(str(ex.get("key")), ""))
        excludes = ex.get("mode") == "exclude"
        flags.append(
            RedFlag(
                kind="exclusion" if excludes else "penalty",
                key=str(ex["key"]),
                label_pl="Wyklucza lokalizację" if excludes else "Kara punktowa",
                reason_pl=str(ex.get("reason_pl", "")),
                points=0.0 if excludes else float(ex.get("penalty_points") or 0),
                source_id=fact.get("source_id") if fact else None,
                as_of=fact.get("as_of") if fact else None,
                confidence=fact.get("confidence") if fact else None,
            )
        )
    for item in score.get("items") or []:
        if item.get("category") == "penalty" and float(item.get("points") or 0) < 0:
            flags.append(
                RedFlag(
                    kind="penalty",
                    key=str(item["key"]),
                    label_pl=str(item.get("label_pl", item["key"])),
                    reason_pl=str(item.get("explanation_pl", "")),
                    points=-float(item["points"]),
                    source_id=item.get("source_id"),
                    as_of=item.get("as_of"),
                    confidence=item.get("confidence"),
                )
            )
    return flags


@dataclass(frozen=True)
class FactRow:
    key: str
    label_pl: str
    value: float | int | bool | str | None
    unit: str | None
    source_id: str | None
    as_of: str | None
    confidence: Literal["verified", "estimated", "missing"]


# (feature key, label, unit, source shown when the value is missing). The order is the order of the table.
FACT_SPECS: tuple[tuple[str, str, str | None, str], ...] = (
    ("area_ha", "Powierzchnia", "ha", "egib"),
    ("parcel_count", "Liczba działek ewidencyjnych", None, "egib"),
    ("compactness", "Zwartość kształtu (0–1)", None, "egib"),
    ("expansion_ha", "Sąsiednie działki na rozbudowę", "ha", "egib"),
    ("dist_line110_km", "Odległość do linii 110 kV", "km", "osm_power"),
    ("dist_gpz110_km", "Odległość do GPZ 110/SN", "km", "osm_power"),
    ("independent_110_lines_near", "Niezależne linie 110 kV w pobliżu (N-1)", None, "osm_power"),
    ("dist_node_220_400_km", "Odległość do węzła 220/400 kV", "km", "osm_power"),
    ("dist_other_station_km", "Odległość do stacji wytwórcy lub odbiorcy", "km", "osm_power"),
    ("powered_brownfield", "Teren poprzemysłowy z własną stacją", None, "osm_power"),
    ("dist_pse_planned_station_km", "Odległość do planowanej stacji PSE", "km", "pse_avail"),
    ("dist_pse_odb_km", "Odległość do wpisu ODB PSE", "km", "pse_odb"),
    ("closed_switchyard_nearby", "Zamknięta rozdzielnia PSE w pobliżu", None, "pse_closed"),
    ("published_gpz_headroom_mw", "Opublikowana moc GPZ (OSD)", "MW", "dso_portals"),
    ("zoning", "Przeznaczenie terenu", None, "ru"),
    ("planning_path", "Ścieżka planistyczna", None, "ru"),
    ("in_ouz", "Obszar uzupełnienia zabudowy (OUZ)", None, "ru"),
    ("plan_ogolny_act", "Uchwała planu ogólnego", None, "ru"),
    ("plan_ogolny_from", "Plan ogólny obowiązuje od", None, "ru"),
    ("zone_max_height_m", "Maksymalna wysokość zabudowy", "m", "ru"),
    ("zone_max_build_up_pct", "Maksymalna powierzchnia zabudowy", "%", "ru"),
    ("zone_min_pbc_pct", "Minimalny udział powierzchni biologicznie czynnej", "%", "ru"),
    ("protected_nature_pct", "Natura 2000 i obszary chronione", "% terenu", "gdos"),
    ("landscape_park_or_ochk", "Park krajobrazowy lub OChK", None, "gdos"),
    ("flood_10_or_1_pct", "Strefa zalewowa 10% / 1%", "% terenu", "isok"),
    ("flood_0_2_pct", "Strefa zalewowa 0,2%", "% terenu", "isok"),
    ("forest_pct", "Las", "% terenu", "bdot10k"),
    ("deposit_overlap_pct", "Złoże kopaliny", "% terenu", "midas"),
    ("deposit_list", "Złoża pod terenem", None, "midas"),
    ("mining_area_pct", "Obszar górniczy", "% terenu", "midas"),
    ("mining_terrain_pct", "Teren górniczy", "% terenu", "midas"),
    ("soil_class_i_ii_pct", "Gleby klas I–II", "% terenu", "kiug"),
    ("soil_class_iii_pct", "Gleby klasy III", "% terenu", "kiug"),
    ("on_heritage_register", "Rejestr zabytków", None, "nid"),
    ("residential_min_m", "Odległość do zabudowy mieszkaniowej", "m", "bdot10k"),
    ("dist_public_road_m", "Odległość do drogi publicznej", "m", "bdot10k"),
    ("gas_pipeline_zone", "Strefa gazociągu (szacunek z OSM)", None, "osm_power"),
    ("airport_ols", "Powierzchnie ograniczające lotniska (szacunek z OSM)", None, "osm_power"),
    ("dist_telecom_node_km", "Odległość do węzła telekomunikacyjnego", "km", "uke"),
    ("dist_big_city_km", "Odległość do dużego miasta", "km", "prg"),
    ("dist_heat_network_km", "Odległość do sieci ciepłowniczej", "km", "heat"),
    ("dist_res_cluster_km", "Odległość do skupiska OZE", "km", "osm_power"),
    ("env_decision_found", "Decyzja środowiskowa", None, "bazaoos"),
    ("public_seller", "Sprzedający jest podmiotem publicznym", None, "kowr"),
)


def _flat_value(value: Any) -> float | int | bool | str | None:
    """Scalars pass (floats rounded to 3 places); a list of deposits reads as their names."""
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, Sequence):
        names = [str(d.get("name", d)) if isinstance(d, Mapping) else str(d) for d in value]
        return ", ".join(names) if names else "brak"
    return str(value)


def facts_table(detail: Mapping[str, Any], *, scored_at: str | None = None) -> list[FactRow]:
    """Every known fact of the site with its source, date and confidence; unknown ones as `missing`.

    The capacity rows come first. Land capacity is area x density, so it is only an estimate; `scored_at` is
    the date of the scoring run that produced it.
    """
    score = detail.get("score") or {}
    features = detail.get("features") or {}
    rows: list[FactRow] = []
    for key, label in (
        ("project_mw", "Moc projektu (mniejsza z pojemności terenu i celu)"),
        ("estimated_mw", "Pojemność terenu (powierzchnia × gęstość z ustawień)"),
    ):
        value = score.get(key)
        known = value is not None
        rows.append(
            FactRow(
                key=key,
                label_pl=label,
                value=_flat_value(value),
                unit="MW",
                source_id="egib" if known else None,
                as_of=scored_at if known else None,
                confidence="estimated" if known else "missing",
            )
        )
    for key, label, unit, missing_source in FACT_SPECS:
        fact = features.get(key)
        if not isinstance(fact, Mapping):
            rows.append(FactRow(key, label, None, unit, missing_source, None, "missing"))
            continue
        rows.append(
            FactRow(
                key=key,
                label_pl=label,
                value=_flat_value(fact.get("value")),
                unit=unit,
                source_id=fact.get("source_id"),
                as_of=fact.get("as_of"),
                confidence=fact.get("confidence", "estimated"),
            )
        )
    return rows
