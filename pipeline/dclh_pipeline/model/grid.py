"""Grid model: classify substations and derive the distribution operator (brief section 5, step 2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from dclh_pipeline.model.provenance import Confidence

# Voltage thresholds in volts. "MV" means anything below 110 kV that is still a distribution level.
V_110 = 110_000
V_220 = 220_000
V_MV_MIN = 1_000


class SubstationClass(StrEnum):
    GPZ_110_SN = "GPZ_110_SN"  # 110 kV plus a medium-voltage level: a "GPZ"
    NODE_220_400 = "NODE_220_400"  # PSE transmission node (220 kV or 400 kV present)
    OTHER = "OTHER"  # known voltages, none of the above (e.g. MV only, or a 110 kV-only switchyard)
    UNKNOWN_VOLTAGE = "UNKNOWN_VOLTAGE"  # no usable voltage tag; kept at low confidence, never dropped


class Dso(StrEnum):
    ENEA = "enea"
    ENERGA = "energa"
    TAURON = "tauron"
    PGE = "pge"
    PSE = "pse"  # transmission operator, not a DSO, but handy as an operator class


_OPERATOR_PATTERNS: tuple[tuple[re.Pattern[str], Dso], ...] = (
    (re.compile(r"\benea\b", re.I), Dso.ENEA),
    (re.compile(r"\benerga\b", re.I), Dso.ENERGA),
    (re.compile(r"\btauron\b", re.I), Dso.TAURON),
    (re.compile(r"\bpge\b|polska grupa energetyczna", re.I), Dso.PGE),
    (re.compile(r"\bpse\b|polskie sieci elektroenergetyczne", re.I), Dso.PSE),
)


def parse_voltages(tag: str | None) -> list[int]:
    """Parse an OSM `voltage` tag ("110000;15000", "400000", "110 kV") into volts, descending.

    Unparsable parts are ignored; an empty list means "no usable voltage".
    """
    if not tag:
        return []
    volts: list[int] = []
    for part in re.split(r"[;,/]", tag):
        part = part.strip().lower().replace(" ", "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(kv|v)?", part)
        if not m:
            continue
        value = float(m.group(1))
        if m.group(2) == "kv":
            value *= 1000
        elif m.group(2) is None and value < 1000:
            # A bare number below 1000 is almost certainly kV ("110"); real OSM values are volts.
            value *= 1000
        volts.append(round(value))
    return sorted(set(volts), reverse=True)


@dataclass(frozen=True)
class Classification:
    cls: SubstationClass
    voltages: tuple[int, ...]
    confidence: Confidence
    reason: str


def classify_substation(voltage_tag: str | None) -> Classification:
    volts = parse_voltages(voltage_tag)
    if not volts:
        return Classification(
            SubstationClass.UNKNOWN_VOLTAGE, (), Confidence.MISSING, "brak lub nieczytelny tag voltage"
        )
    top = volts[0]
    has_110 = any(V_110 <= v < V_220 for v in volts)
    has_mv = any(V_MV_MIN <= v < V_110 for v in volts)
    if top >= V_220:
        return Classification(
            SubstationClass.NODE_220_400, tuple(volts), Confidence.ESTIMATED, "napięcie ≥ 220 kV"
        )
    if has_110 and has_mv:
        return Classification(SubstationClass.GPZ_110_SN, tuple(volts), Confidence.ESTIMATED, "110 kV + SN")
    if has_110:
        return Classification(
            SubstationClass.OTHER, tuple(volts), Confidence.ESTIMATED, "tylko 110 kV (bez poziomu SN w tagu)"
        )
    return Classification(
        SubstationClass.OTHER, tuple(volts), Confidence.ESTIMATED, "napięcie poniżej 110 kV"
    )


def derive_operator(operator_tag: str | None) -> Dso | None:
    """Map the free-text `operator` tag to a known operator, or None (e.g. an industrial owner)."""
    if not operator_tag:
        return None
    for pattern, dso in _OPERATOR_PATTERNS:
        if pattern.search(operator_tag):
            return dso
    return None


def is_foreign_operator(operator_tag: str | None) -> bool:
    """True when an operator is named but is neither a DSO nor PSE: a candidate "powered brownfield"."""
    return bool(operator_tag and operator_tag.strip()) and derive_operator(operator_tag) is None
