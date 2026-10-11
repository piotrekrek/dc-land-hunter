"""Map planning-zone codes to `ZoningKind` (brief section 3, zoning).

Codes come from the APP (MPZP and plan ogólny GML) and their spelling varies between gminas ("PU1",
"P/U.2", "p.u."). The mapping works on the base code only. The check against the APP schema still has to run
on real data. Until then unknown codes map to `OTHER` (scores 0), and the test table documents the assumption.
"""

from __future__ import annotations

import re

from dclh_pipeline.scoring.types import ZoningKind

# Production and service designations: P, PU, P/U, U/P, plus energy and technical infrastructure.
PRODUCTION_CODES = frozenset({"P", "PU", "P/U", "U/P", "E", "IT"})
PLAN_OGOLNY_SG_CODES = frozenset({"SG"})

_BASE_CODE = re.compile(r"^([A-Z]+(?:/[A-Z]+)?)")


def base_code(raw: str) -> str | None:
    """'PU1' → 'PU', 'p/u.2' → 'P/U', 'P.1' → 'P'. None when there is no letter code to read."""
    normalised = re.sub(r"\s+", "", raw).upper()
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
    if code in PLAN_OGOLNY_SG_CODES:
        return ZoningKind.PLAN_OGOLNY_SG
    return ZoningKind.OTHER
