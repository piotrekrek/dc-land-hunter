"""Sprawdź działkę: resolve a parcel, start a check, follow its progress and read the result (PLAN.md)."""

from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.routers.sites import _SUMMARY_COLUMNS, _site_detail
from dclh_api.schemas import SiteDetail
from dclh_api.security import current_user
from dclh_pipeline.check_job import DEFAULT_STEP_SECONDS, HISTORY, build_client, create_check
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.orthophoto import OrthophotoUnavailable, fetch_orthophoto, request_for_sheet
from dclh_pipeline.parcel_check import load_aoi, resolve_parcels
from dclh_pipeline.parcel_input import MAX_INPUTS, parse_inputs
from dclh_pipeline.settings import get_settings

router = APIRouter(tags=["checks"], dependencies=[Depends(current_user)])
logger = logging.getLogger(__name__)

MAX_RUNNING_PER_USER = 3
RECENT_CHECKS = 30


class ResolveIn(BaseModel):
    text: str = Field(max_length=4000)


class ResolvedParcelOut(BaseModel):
    parcel_id: str
    powiat_teryt: str
    voivodeship: str
    county: str
    commune: str
    obreb: str
    area_ha: float
    lat: float
    lon: float
    in_aoi: bool | None  # None: the area of interest is not loaded
    source_text: str
    outline: dict[str, Any]  # GeoJSON polygon, WGS84


class IssueOut(BaseModel):
    text: str
    message_pl: str


class ResolveOut(BaseModel):
    items: list[ResolvedParcelOut]
    issues: list[IssueOut]
    dropped: int
    duplicates: int


class PointIn(BaseModel):
    lat: float = Field(ge=48.5, le=55.5)
    lon: float = Field(ge=13.5, le=24.5)


class CheckIn(BaseModel):
    parcels: list[str] = Field(default_factory=list, max_length=MAX_INPUTS)
    points: list[PointIn] = Field(default_factory=list, max_length=MAX_INPUTS)

    @field_validator("parcels")
    @classmethod
    def _strip(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if v.strip()]


class CreatedOut(BaseModel):
    id: str


class StepOut(BaseModel):
    parcel: int | None
    key: str
    label_pl: str
    status: Literal["waiting", "running", "done", "failed"]
    seconds: float | None
    finding_pl: str | None
    error: str | None


class VerdictOut(BaseModel):
    kind: Literal["odpada", "wymaga_wyjasnien", "warto"]
    text_pl: str


class CheckSiteSummary(BaseModel):
    n: int
    parcel_id: str
    commune: str
    obreb: str
    area_ha: float
    estimated_mw: float
    score: float
    upside_score: float
    completeness: float
    excluded: bool
    verdict: VerdictOut
    outside_aoi: bool


class CheckOut(BaseModel):
    id: str
    status: Literal["pending", "running", "done", "failed"]
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    parcels: list[str]  # what was asked: parcel ids, or the coordinates of a point
    steps: list[StepOut]
    estimate_seconds: int | None  # remaining time while the check is open
    sites: list[CheckSiteSummary]


class CheckListItem(BaseModel):
    id: str
    status: Literal["pending", "running", "done", "failed"]
    requested_by: str
    created_at: datetime
    parcels: list[str]
    verdict: VerdictOut | None  # the first finished parcel's
    score: float | None


class CheckList(BaseModel):
    items: list[CheckListItem]


class UnknownOut(BaseModel):
    label_pl: str
    why_pl: str


class NextStepOut(BaseModel):
    key: str
    title_pl: str
    copy_text: str | None


class ParcelInfoOut(BaseModel):
    parcel_id: str
    voivodeship: str
    county: str
    commune: str
    obreb: str
    powiat_teryt: str
    registered_area_ha: float


class SheetLayerOut(BaseModel):
    key: str
    label_pl: str
    kind: str
    paths: list[list[list[float]]]
    truncated: bool


class SheetOut(BaseModel):
    origin_2180: list[float]
    bbox_m: list[float]
    parcel: list[list[list[float]]]
    layers: list[SheetLayerOut]


class CheckSiteDetail(SiteDetail):
    """A scored parcel: what the site card shows, plus the verdict, the sheet and the manual next steps."""

    n: int
    check_id: str
    requested_by: str
    checked_at: datetime
    verdict: VerdictOut
    outside_aoi: bool
    unknowns: list[UnknownOut]
    next_steps: list[NextStepOut]
    sheet: SheetOut
    layer_ages: dict[str, datetime | None]
    parcel: ParcelInfoOut


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Baza danych jest niedostępna.")


# ---------------------------------------------------------------------------------------------- resolve


@router.post("/parcels/resolve", response_model=ResolveOut)
def resolve(body: ResolveIn, session: Annotated[Session, Depends(db_session)]) -> ResolveOut:
    """Ask ULDK what was pasted before any expensive work ("Czy to ta działka?"). Short and synchronous."""
    parsed = parse_inputs(body.text)
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise HTTPException(503, "Serwer nie ma ustawionego adresu kontaktowego (DCLH_CONTACT_EMAIL).")
    client, _ = build_client(settings)
    try:
        aoi = load_aoi(session)
    except SQLAlchemyError:
        aoi = None  # a missing area of interest only removes the label, it must not stop the lookup
    with client:
        resolution = resolve_parcels(client, parsed, aoi)
    return ResolveOut(
        items=[_outline(p) for p in resolution.items],
        issues=[IssueOut(text=i.text, message_pl=i.message_pl) for i in resolution.issues],
        dropped=resolution.dropped,
        duplicates=resolution.duplicates,
    )


def _outline(parcel: Any) -> ResolvedParcelOut:
    from pyproj import Transformer
    from shapely.geometry import mapping
    from shapely.ops import transform

    to_wgs = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True).transform
    return ResolvedParcelOut(
        parcel_id=parcel.parcel_id,
        powiat_teryt=parcel.powiat_teryt,
        voivodeship=parcel.voivodeship,
        county=parcel.county,
        commune=parcel.commune,
        obreb=parcel.obreb,
        area_ha=round(parcel.area_ha, 4),
        lat=parcel.lat,
        lon=parcel.lon,
        in_aoi=parcel.in_aoi,
        source_text=parcel.source_text,
        outline=dict(mapping(transform(to_wgs, parcel.geom.simplify(0.5)))),
    )


# ---------------------------------------------------------------------------------------------- create


@router.post("/checks", response_model=CreatedOut, status_code=status.HTTP_202_ACCEPTED)
def start_check(
    body: CheckIn,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> CreatedOut:
    """Queue a check. The worker does the work; the page polls `GET /api/checks/{id}`."""
    inputs: list[dict[str, Any]] = []
    if body.parcels:
        parsed = parse_inputs("\n".join(body.parcels))
        wrong = [i.text for i in parsed.issues] + [p.text for p in parsed.items if p.kind != "id"]
        if wrong:
            raise HTTPException(422, f"To nie są identyfikatory działek: {', '.join(wrong)}.")
        inputs += [{"kind": "id", "parcel_id": p.parcel_id, "text": p.text} for p in parsed.items]
    inputs += [
        {"kind": "point", "lat": p.lat, "lon": p.lon, "text": f"{p.lat:.5f} N, {p.lon:.5f} E"}
        for p in body.points
    ]
    if not inputs:
        raise HTTPException(422, "Podaj co najmniej jedną działkę.")
    if len(inputs) > MAX_INPUTS:
        raise HTTPException(422, f"Najwyżej {MAX_INPUTS} działek naraz.")
    try:
        running = session.execute(
            text(
                "SELECT count(*) FROM parcel_checks "
                "WHERE requested_by = :u AND status IN ('pending', 'running')"
            ),
            {"u": user.email},
        ).scalar_one()
        if running >= MAX_RUNNING_PER_USER:
            raise HTTPException(429, f"Masz już {running} sprawdzenia w toku. Poczekaj, aż się skończą.")
        check_id = create_check(session, user.email, inputs)
        session.commit()
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    return CreatedOut(id=check_id)


# ---------------------------------------------------------------------------------------------- read


def _remaining_seconds(session: Session, steps: list[dict[str, Any]], parcels: int) -> int:
    """Time left: the median of the last runs of each step still to do, else the spike's defaults."""
    if not steps:  # the job has not started: assume every step of every parcel, and the static layers
        pending = [("static", 1)] + [(k, parcels) for k in DEFAULT_STEP_SECONDS if k != "static"]
    else:
        pending = [(s["key"], 1) for s in steps if s["status"] in ("waiting", "running")]
    total = 0.0
    for key, times in pending:
        rows = session.execute(
            text("SELECT seconds FROM check_step_stats WHERE step_key = :k ORDER BY at DESC LIMIT :n"),
            {"k": key, "n": HISTORY},
        ).scalars()
        seconds = [float(v) for v in rows]
        total += (statistics.median(seconds) if seconds else DEFAULT_STEP_SECONDS.get(key, 1.0)) * times
    return round(total)


@router.get("/checks", response_model=CheckList)
def recent_checks(
    session: Annotated[Session, Depends(db_session)], user: Annotated[UserRow, Depends(current_user)]
) -> CheckList:
    """A member sees their own checks; an admin sees everyone's."""
    try:
        rows = session.execute(
            text(
                "SELECT c.id::text AS id, c.status, c.requested_by, c.created_at, c.inputs, "
                "s.verdict, s.score FROM parcel_checks c "
                "LEFT JOIN parcel_check_sites s ON s.check_id = c.id AND s.n = 0 "
                "WHERE (:all OR c.requested_by = :me) "
                "ORDER BY c.created_at DESC LIMIT :n"
            ),
            {"n": RECENT_CHECKS, "all": user.role == "admin", "me": user.email},
        ).mappings()
        items = [
            CheckListItem(
                id=r["id"],
                status=r["status"],
                requested_by=r["requested_by"],
                created_at=r["created_at"],
                parcels=[str(i.get("parcel_id") or i.get("text", "")) for i in r["inputs"]],
                verdict=VerdictOut(**r["verdict"]) if r["verdict"] else None,
                score=r["score"],
            )
            for r in rows
        ]
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    return CheckList(items=items)


def _check_row(session: Session, check_id: str, user: UserRow) -> dict[str, Any]:
    try:
        row = (
            session.execute(
                text(
                    "SELECT id::text AS id, requested_by, created_at, started_at, finished_at, status, "
                    "error, steps, inputs FROM parcel_checks WHERE id = CAST(:id AS uuid)"
                ),
                {"id": check_id},
            )
            .mappings()
            .first()
        )
    except SQLAlchemyError as exc:  # also a malformed id: PostgreSQL rejects the cast
        session.rollback()
        raise HTTPException(404, "Nie ma takiego sprawdzenia.") from exc
    if row is None:
        raise HTTPException(404, "Nie ma takiego sprawdzenia.")
    # Somebody else's check is "not found", not "forbidden": a member learns nothing about other checks.
    if user.role != "admin" and row["requested_by"] != user.email:
        raise HTTPException(404, "Nie ma takiego sprawdzenia.")
    return dict(row)


@router.get("/checks/{check_id}", response_model=CheckOut)
def get_check(
    check_id: str,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> CheckOut:
    row = _check_row(session, check_id, user)
    steps = list(row["steps"])
    open_check = row["status"] in ("pending", "running")
    sites = session.execute(
        text(
            "SELECT n, parcel, area_ha, estimated_mw, score, upside_score, completeness, excluded, verdict, "
            "outside_aoi FROM parcel_check_sites WHERE check_id = CAST(:id AS uuid) ORDER BY n"
        ),
        {"id": check_id},
    ).mappings()
    return CheckOut(
        id=row["id"],
        status=row["status"],
        requested_by=row["requested_by"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error=row["error"],
        parcels=[str(i.get("parcel_id") or i.get("text", "")) for i in row["inputs"]],
        steps=[StepOut(**s) for s in steps],
        estimate_seconds=_remaining_seconds(session, steps, len(row["inputs"])) if open_check else None,
        sites=[
            CheckSiteSummary(
                n=s["n"],
                parcel_id=s["parcel"]["parcel_id"],
                commune=s["parcel"]["commune"],
                obreb=s["parcel"]["obreb"],
                area_ha=s["area_ha"],
                estimated_mw=s["estimated_mw"],
                score=s["score"],
                upside_score=s["upside_score"],
                completeness=s["completeness"],
                excluded=s["excluded"],
                verdict=VerdictOut(**s["verdict"]),
                outside_aoi=s["outside_aoi"],
            )
            for s in sites
        ],
    )


@router.get("/checks/{check_id}/sites/{n}", response_model=CheckSiteDetail)
def get_check_site(
    check_id: str,
    n: int,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> CheckSiteDetail:
    row = _check_row(session, check_id, user)
    site = (
        session.execute(
            text(
                f"SELECT {_SUMMARY_COLUMNS}, geom, parcel_ids, scored_at, "
                "ST_AsGeoJSON(ST_Transform(geom, 4326)) AS geojson, verdict, outside_aoi, unknowns, "
                "next_steps, sheet, layer_ages, parcel FROM parcel_check_sites "
                "WHERE check_id = CAST(:id AS uuid) AND n = :n"
            ),
            {"id": check_id, "n": n},
        )
        .mappings()
        .first()
    )
    if site is None:
        raise HTTPException(404, "Nie ma takiej działki w tym sprawdzeniu.")
    try:
        base = _site_detail(site)
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception("Malformed check site %s/%s", check_id, n)
        raise HTTPException(500, "Nie udało się wczytać wyniku tego sprawdzenia.") from exc
    return CheckSiteDetail(
        **base.model_dump(),
        n=n,
        check_id=check_id,
        requested_by=row["requested_by"],
        checked_at=site["scored_at"],
        verdict=VerdictOut(**site["verdict"]),
        outside_aoi=site["outside_aoi"],
        unknowns=[UnknownOut(**u) for u in site["unknowns"]],
        next_steps=[NextStepOut(**s) for s in site["next_steps"]],
        sheet=SheetOut(**site["sheet"]),
        layer_ages={k: v for k, v in site["layer_ages"].items()},
        parcel=ParcelInfoOut(**site["parcel"]),
    )


@router.get(
    "/checks/{check_id}/sites/{n}/orthophoto",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def get_check_orthophoto(
    check_id: str,
    n: int,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> Response:
    """The Geoportal orthophoto for exactly the sheet's box, asked for only when the sheet shows it."""
    _check_row(session, check_id, user)
    sheet = session.execute(
        text("SELECT sheet FROM parcel_check_sites WHERE check_id = CAST(:id AS uuid) AND n = :n"),
        {"id": check_id, "n": n},
    ).scalar_one_or_none()
    if sheet is None:
        raise HTTPException(404, "Nie ma takiej działki w tym sprawdzeniu.")
    try:
        wanted = request_for_sheet(tuple(sheet["origin_2180"]), tuple(sheet["bbox_m"]))
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception("Sheet of check %s/%s has no usable box", check_id, n)
        raise HTTPException(404, "Ten rysunek nie ma ortofotomapy.") from exc
    settings = get_settings()
    _, cache = build_client(settings)
    try:
        picture = fetch_orthophoto(wanted, user_agent=settings.user_agent, cache=cache)
    except OrthophotoUnavailable as exc:
        raise HTTPException(
            502, "Geoportal nie zwrócił ortofotomapy dla tego miejsca. Spróbuj za chwilę."
        ) from exc
    return Response(
        picture,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
