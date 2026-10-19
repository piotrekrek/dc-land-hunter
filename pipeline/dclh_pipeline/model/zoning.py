"""Map planning-zone codes to `ZoningKind` (brief section 3, zoning).

MPZP designation codes come from KIMPZP and their spelling varies between gminas ("PU1", "P/U.2", "p.u.",
Warsaw's "3k U-O/US"). The mapping works on the base code only, and unknown codes map to `OTHER` (scores 0).

Plan ogólny zones come from the Rejestr Urbanistyczny (APP GML). Checked on real data (19.10.2026, around
Pruszków): the strefa gospodarcza has the symbol **SP** and `nazwa` = `.../RodzajStrefyPlanistycznejKod/
strefaGospodarcza`. SG is a different zone (no SG zone in 679 near Pruszków). So plan ogólny zones are read by
`nazwa`, not by symbol.
"""

from __future__ import annotations

import re

from dclh_pipeline.scoring.types import ZoningKind

# Production and service designations: P, PU, P/U (or P-U), U/P, plus energy and technical infrastructure.
PRODUCTION_CODES = frozenset({"P", "PU", "P/U", "U/P", "E", "IT"})

# APP code lists (the end of the xlink href).
PLAN_OGOLNY_ECONOMIC_ZONE = "strefaGospodarcza"
STATUS_IN_FORCE = "legalForce"
STATUS_PROCEDURE = frozenset({"elaboration", "adoption"})

_BASE_CODE = re.compile(r"^([A-Z]+(?:/[A-Z]+)?)")
# The plan's area number in front of the code: "3k U-O/US", "2 KDD", "12.MN", "1PU". A lower-case letter after
# the number is part of the number only when a space follows it ("3k U"), so "1pu" still reads as PU.
_AREA_NUMBER = re.compile(r"^\d+(?:[a-z](?=\s))?[\s.]*")


def base_code(raw: str) -> str | None:
    """'PU1' → 'PU', 'p/u.2' → 'P/U', 'P-U' → 'P/U', '3k U-O/US' → 'U/O'. None without a letter code."""
    normalised = re.sub(r"\s+", "", _AREA_NUMBER.sub("", raw.strip())).upper().replace("-", "/")
    match = _BASE_CODE.match(normalised)
    return match.group(1) if match else None


def zoning_kind_from_code(raw: str, *, procedure_in_progress: bool = False) -> ZoningKind:
    """The kind of zone for one register entry.

    `procedure_in_progress` marks a zone whose plan is being prepared (the register reports the procedure
    status separately). A procedure outranks the code: a zone that is only being planned is not yet usable.
    """
    if procedure_in_progress:
        return ZoningKind.PROCEDURE_IN_PROGRESS
    code = base_code(raw)
    if code is None:
        return ZoningKind.OTHER
    if code in PRODUCTION_CODES:
        return ZoningKind.MPZP_PRODUCTION
    return ZoningKind.OTHER


def _code_list_value(href: str | None) -> str:
    return (href or "").rstrip("/").rsplit("/", 1)[-1]


def plan_ogolny_kind(zone_href: str | None, status_href: str | None) -> ZoningKind | None:
    """The kind of one plan ogólny zone, or None when the zone should not decide the zoning.

    In force: a strefa gospodarcza is `PLAN_OGOLNY_SG`, any other zone is `OTHER` (a future MPZP must follow
    it). In procedure: a strefa gospodarcza is `PROCEDURE_IN_PROGRESS`. Any other draft zone is None: a draft
    is not binding, and the land keeps whatever it has today.
    """
    economic = _code_list_value(zone_href) == PLAN_OGOLNY_ECONOMIC_ZONE
    status = _code_list_value(status_href)
    if status == STATUS_IN_FORCE:
        return ZoningKind.PLAN_OGOLNY_SG if economic else ZoningKind.OTHER
    if status in STATUS_PROCEDURE and economic:
        return ZoningKind.PROCEDURE_IN_PROGRESS
    return None


def mpzp_outline_kind(status_href: str | None) -> ZoningKind | None:
    """An MPZP outline from the register: a plan in procedure is `PROCEDURE_IN_PROGRESS`. Otherwise None.

    The outline has no designations, so a plan in force says only "a plan exists"; KIMPZP has to tell what it
    allows.
    """
    return ZoningKind.PROCEDURE_IN_PROGRESS if _code_list_value(status_href) in STATUS_PROCEDURE else None


def sampled_mpzp_kind(points: list[list[str]]) -> ZoningKind | None:
    """The kind of a site from KIMPZP answers, one list of designation codes per sampled point.

    A point is production when any designation answered there is production. The answer reaches a few metres
    around the point, so a road next to a PU area must not hide it. The site is production when at least half
    of the points that have a plan are production, and `OTHER` when fewer are. None when no point has a plan.
    """
    planned = [codes for codes in points if codes]
    if not planned:
        return None
    production = sum(
        1 for codes in planned if any(zoning_kind_from_code(c) is ZoningKind.MPZP_PRODUCTION for c in codes)
    )
    return ZoningKind.MPZP_PRODUCTION if 2 * production >= len(planned) else ZoningKind.OTHER
