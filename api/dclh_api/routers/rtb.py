"""RTB evidence export of one site: a JSON annex the web page prints to PDF (PLAN.md, Wave 3, task 9).

Every value carries its source, date and confidence. The workstream statuses are a proposal computed from the
stored facts and the manual power evidence; the team decides.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from shapely import wkb
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.mcp.tools_costs import AS_OF as COST_AS_OF
from dclh_api.mcp.tools_costs import CAVEATS as COST_CAVEATS
from dclh_api.mcp.tools_costs import SOURCE_ID as COST_SOURCE_ID
from dclh_api.mcp.tools_costs import compute as compute_connection_cost
from dclh_api.prices import site_prices
from dclh_api.routers.checks import SheetOut
from dclh_api.routers.sites import _site_detail, _site_row, _unavailable, load_power_evidence
from dclh_api.schemas import Confidence, PowerEvidenceOut, SitePrices, SiteSummary
from dclh_api.security import current_user
from dclh_pipeline.check_sheet import build_sheet, sheet_bounds
from dclh_pipeline.config import ConnectionCost, load_scoring
from dclh_pipeline.rtb import PowerEvidence, propose_rag
from dclh_pipeline.rtb_facts import facts_table, red_flags

router = APIRouter(tags=["rtb"], dependencies=[Depends(current_user)])

# The drawing reaches far enough to show the 110 kV lines and substations around the site.
RTB_MAP_MARGIN_M = 2500.0
MAP_NOTE_PL = (
    "Mapa pokazuje obrys terenu, linie 110 kV i wyższe oraz stacje z bazy (OSM). "
    "Obrysy lasu, strefy zalewowej, "
    "Natura 2000 i złóż nie są zapisane w bazie: ich udział w terenie podaje tabela faktów."
)
STATUS_NOTE_PL = (
    "Statusy to propozycja wyliczona z zapisanych danych i ręcznie wpisanego dowodu mocy. "
    "Decyzję podejmuje zespół."
)


class RtbReason(BaseModel):
    text_pl: str
    source_id: str | None
    as_of: str | None


class RtbWorkstream(BaseModel):
    key: str
    label_pl: str
    status: Literal["green", "yellow", "red"]
    reasons: list[RtbReason]


class RtbRedFlag(BaseModel):
    kind: Literal["exclusion", "penalty"]
    key: str
    label_pl: str
    reason_pl: str
    points: float
    source_id: str | None
    as_of: str | None
    confidence: str | None


class RtbFact(BaseModel):
    key: str
    label_pl: str
    value: float | int | bool | str | None
    unit: str | None
    source_id: str | None
    as_of: str | None
    confidence: Confidence


class RtbCostLine(BaseModel):
    key: Literal["application_fee", "advance", "security"]
    label_pl: str
    pln: float
    basis_pl: str
    source_id: str
    as_of: str
    confidence: Confidence


class RtbConnectionCost(BaseModel):
    mw: float
    lines: list[RtbCostLine]
    frozen_pln: float
    total_pln: float
    conditions_valid_months: int
    legal_basis: str
    verified: bool
    caveats_pl: list[str]


class RtbExport(BaseModel):
    generated_at: datetime
    site: SiteSummary
    parcel_ids: list[str]
    scored_at: datetime
    power_evidence: PowerEvidenceOut
    status_note_pl: str
    workstreams: list[RtbWorkstream]
    red_flags: list[RtbRedFlag]
    connection_cost: RtbConnectionCost
    price_comparables: SitePrices | None = None  # RCN land prices near the site (informational)
    facts: list[RtbFact]
    map: SheetOut
    map_note_pl: str


def _zl(value: float) -> str:
    """Whole złoty with a space as the thousands separator: 100 000 zł."""
    return f"{value:,.0f}".replace(",", " ") + " zł"


def _cost(rules: ConnectionCost, mw: float) -> RtbConnectionCost:
    raw = compute_connection_cost(rules, mw)
    confidence: Confidence = "verified" if rules.verified else "estimated"
    meta = {"source_id": COST_SOURCE_ID, "as_of": COST_AS_OF, "confidence": confidence}
    fee_rate = rules.application_fee_pln_per_kw
    lines = [
        RtbCostLine(
            key="application_fee",
            label_pl="Opłata za wniosek (wydana)",
            pln=raw["application_fee"]["pln"],
            basis_pl=f"{fee_rate:g} zł/kW, maks. {_zl(rules.application_fee_max_pln)}",
            **meta,
        ),
        RtbCostLine(
            key="advance",
            label_pl="Zaliczka na przyłączenie (zamrożona)",
            pln=raw["advance"]["pln"],
            basis_pl=f"{rules.advance_pln_per_kw:g} zł/kW, maks. {_zl(rules.advance_max_pln)}",
            **meta,
        ),
        RtbCostLine(
            key="security",
            label_pl="Zabezpieczenie (zamrożone)",
            pln=raw["security"]["pln"],
            basis_pl=f"{rules.security_pln_per_kw:g} zł/kW do {rules.security_applies_up_to_mw:g} MW",
            **meta,
        ),
    ]
    return RtbConnectionCost(
        mw=mw,
        lines=lines,
        frozen_pln=raw["frozen_pln"],
        total_pln=raw["total_pln"],
        conditions_valid_months=rules.conditions_valid_months,
        legal_basis=rules.legal_basis,
        verified=rules.verified,
        caveats_pl=COST_CAVEATS,
    )


def _grid_layers(session: Session, parcel: BaseGeometry) -> list[tuple[str, str, str, list[BaseGeometry]]]:
    """110 kV lines and substations in the drawing window, from the grid tables (OSM)."""
    x0, y0, x1, y1 = sheet_bounds(parcel, RTB_MAP_MARGIN_M)
    window = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    box = "ST_MakeEnvelope(:x0, :y0, :x1, :y1, 2180)"

    def geoms(sql: str) -> list[BaseGeometry]:
        return [wkb.loads(bytes(r[0])) for r in session.execute(text(sql), window)]

    return [
        (
            "lines110",
            "Linie 110 kV i wyższe",
            "line",
            geoms(f"SELECT ST_AsBinary(geom) FROM lines WHERE voltage >= 110000 AND geom && {box}"),
        ),
        (
            "gpz",
            "GPZ 110/SN",
            "point",
            geoms(f"SELECT ST_AsBinary(geom) FROM substations WHERE cls = 'GPZ_110_SN' AND geom && {box}"),
        ),
        (
            "node",
            "Węzły 220/400 kV",
            "point",
            geoms(f"SELECT ST_AsBinary(geom) FROM substations WHERE cls = 'NODE_220_400' AND geom && {box}"),
        ),
    ]


@router.get("/sites/{site_id}/rtb", response_model=RtbExport)
def get_rtb(site_id: str, session: Annotated[Session, Depends(db_session)]) -> RtbExport:
    row = _site_row(session, site_id)
    if row is None:
        raise HTTPException(404, "Nie ma takiej lokalizacji.")
    try:
        site = _site_detail(row)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(500, "Nie udało się wczytać danych tej lokalizacji.") from exc
    cfg = load_scoring()
    detail = row["detail"]
    try:
        evidence_out = load_power_evidence(session, site_id)
        parcel = wkb.loads(row["geom"], hex=True)
        layers = _grid_layers(session, parcel)
        prices = site_prices(session, site_id)
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    evidence = PowerEvidence(
        level=evidence_out.level,
        document_date=evidence_out.document_date,
        conditions_expiry=evidence_out.conditions_expiry,
        attachment_url=evidence_out.attachment_url,
    )
    scored_at = site.scored_at.isoformat()
    return RtbExport(
        generated_at=datetime.now(UTC),
        site=SiteSummary(**site.model_dump(include=set(SiteSummary.model_fields))),
        parcel_ids=site.parcel_ids,
        scored_at=site.scored_at,
        power_evidence=evidence_out,
        status_note_pl=STATUS_NOTE_PL,
        workstreams=[RtbWorkstream(**asdict(w)) for w in propose_rag(detail, evidence, cfg)],
        red_flags=[RtbRedFlag(**asdict(f)) for f in red_flags(detail)],
        connection_cost=_cost(cfg.connection_cost, site.project_mw),
        price_comparables=prices,
        facts=[RtbFact(**asdict(f)) for f in facts_table(detail, scored_at=scored_at)],
        map=SheetOut(**build_sheet(parcel, layers, margin_m=RTB_MAP_MARGIN_M)),
        map_note_pl=MAP_NOTE_PL,
    )
