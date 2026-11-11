"""What securing a grid connection ties up, for Claude. The figures are the site card's (`scoring.yaml`)."""

from __future__ import annotations

import math
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import text

from dclh_api.mcp.context import run_db
from dclh_pipeline.config import ConnectionCost, load_scoring

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

MAX_MW = 1000
# The rules came into force with the amendment; this is the date every figure is "as of".
AS_OF = "2026-04-30"
SOURCE_ID = "prawo_energetyczne_nowelizacja_2026"

CAVEATS = [
    "Kwoty wymagają weryfikacji prawnej: to interpretacja nowelizacji Prawa energetycznego (od 30.04.2026), "
    "a stawki można zmienić w konfiguracji aplikacji. To nie jest porada prawna.",
    "Przeniesienie warunków przyłączenia na kupującego jest prawnie sporne (wyrok sądu apelacyjnego "
    "z 2013 r. uznał je za prawa publicznoprawne, których nie można przenieść). Zwykle stosuje się obejście: "
    "projekt w spółce celowej (SPV) i sprzedaż jej udziałów.",
    "Zaliczka i zabezpieczenie to pieniądze zamrożone na czas projektu; opłata za wniosek jest wydana.",
]


def _pln(value: float) -> float:
    return round(value, 2)


def compute(rules: ConnectionCost, mw: float) -> dict[str, Any]:
    """Same arithmetic as `connectionCost` in web/src/lib/connectionCost.ts (a test keeps them equal)."""
    kw = mw * 1000
    fee_raw = kw * rules.application_fee_pln_per_kw
    advance_raw = kw * rules.advance_pln_per_kw
    security_kw = min(kw, rules.security_applies_up_to_mw * 1000)
    fee = min(fee_raw, rules.application_fee_max_pln)
    advance = min(advance_raw, rules.advance_max_pln)
    security = security_kw * rules.security_pln_per_kw
    confidence = "verified" if rules.verified else "estimated"
    meta = {"source_id": SOURCE_ID, "as_of": AS_OF, "confidence": confidence}
    return {
        "mw": mw,
        "currency": "PLN",
        "application_fee": {
            "pln": _pln(fee),
            "rate_pln_per_kw": rules.application_fee_pln_per_kw,
            "cap_pln": rules.application_fee_max_pln,
            "cap_applied": fee_raw > rules.application_fee_max_pln,
            **meta,
        },
        "advance": {
            "pln": _pln(advance),
            "rate_pln_per_kw": rules.advance_pln_per_kw,
            "cap_pln": rules.advance_max_pln,
            "cap_applied": advance_raw > rules.advance_max_pln,
            **meta,
        },
        "security": {
            "pln": _pln(security),
            "rate_pln_per_kw": rules.security_pln_per_kw,
            "charged_up_to_mw": rules.security_applies_up_to_mw,
            "cap_applied": mw > rules.security_applies_up_to_mw,
            **meta,
        },
        "frozen_pln": _pln(advance + security),
        "total_pln": _pln(fee + advance + security),
        "frozen_note": "frozen = zaliczka + zabezpieczenie (zamrożone); total = frozen + opłata (wydana).",
        "validity": {
            "conditions_valid_months": rules.conditions_valid_months,
            "milestones": "Umowa wygasa, gdy nie dotrzymasz kamieni milowych pozwolenia na budowę.",
            "re_verification": "Operator weryfikuje warunki ponownie przed podpisaniem umowy.",
            **meta,
        },
        "legal_basis": rules.legal_basis,
        "verified": rules.verified,
        "caveats": CAVEATS,
        **(
            {
                "open_question": f"Zabezpieczenie „do {rules.security_applies_up_to_mw:g} MW”; brief nie "
                "określa powyżej tej mocy. Liczymy jak karta lokalizacji (zabezpieczenie bez zmian). "
                "Do wyjaśnienia prawnego."
            }
            if mw > rules.security_applies_up_to_mw
            else {}
        ),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def connection_costs(
        mw: Annotated[
            float | None, Field(description=f"Requested connection power in MW (above 0, up to {MAX_MW})")
        ] = None,
        site_id: Annotated[
            str | None, Field(description="Site id: with no `mw`, its estimated_mw (land capacity) is used")
        ] = None,
    ) -> dict[str, Any]:
        """What securing a grid connection of `mw` MW ties up: application fee, advance and security, with the
        caps, how long conditions stay valid and the legal caveats. Same figures as the site card.
        These are legal interpretations that need verification, not legal advice."""
        used_site = None
        if mw is None:
            if site_id is None:
                raise ToolError("Podaj moc w MW (mw) albo identyfikator lokalizacji (site_id).")

            def lookup(session: Any, _user: Any) -> float | None:
                row = session.execute(
                    text("SELECT estimated_mw FROM sites WHERE site_id = :id"), {"id": site_id}
                ).scalar()
                return None if row is None else float(row)

            found = await run_db(lookup)
            if found is None:
                raise ToolError(f"Nie znaleziono lokalizacji {site_id}.")
            mw, used_site = found, site_id
        if not math.isfinite(mw) or mw <= 0 or mw > MAX_MW:
            raise ToolError(f"Moc musi być większa od 0 i nie większa niż {MAX_MW} MW (podano {mw}).")
        out = compute(load_scoring().connection_cost, mw)
        if used_site is not None:
            out["site_id"] = used_site
            out["mw_source"] = "estimated_mw lokalizacji (pojemność gruntu, nie moc z sieci)"
        return out
