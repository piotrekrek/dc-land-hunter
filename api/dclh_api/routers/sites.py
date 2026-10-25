"""Ranked candidate sites: the list (filters, sort, pages) and the site card (brief sections 8.1, 6)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from shapely import wkb
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.pagination import CursorError, decode_cursor, encode_cursor
from dclh_api.schemas import FactOut, SiteDetail, SiteMapOut, SitesPage, SiteSummary
from dclh_api.security import current_user
from dclh_pipeline.check_job import build_client
from dclh_pipeline.check_sheet import build_sheet
from dclh_pipeline.orthophoto import OrthophotoUnavailable, fetch_orthophoto, request_for_sheet
from dclh_pipeline.settings import get_settings

router = APIRouter(tags=["sites"], dependencies=[Depends(current_user)])
logger = logging.getLogger(__name__)

# Whitelisted ORDER BY columns: the query string never reaches SQL.
SORT_COLUMNS: dict[str, str] = {
    "score": "score",
    "upside": "upside_score",
    "completeness": "completeness",
    "area": "area_ha",
    "mw": "estimated_mw",
}

_SUMMARY_COLUMNS = """
    site_id, zone_id, area_ha, estimated_mw, score, upside_score, completeness, excluded, grid_badge,
    large_campus, split_suggested, flags, scored_at,
    ST_X(ST_Transform(ST_PointOnSurface(geom), 4326)) AS lon,
    ST_Y(ST_Transform(ST_PointOnSurface(geom), 4326)) AS lat
"""

# The investor profile categories (sites only; parcel checks keep theirs inside `profiles`).
_VERDICT_COLUMNS = "verdict_built, verdict_container"

# What the place join needs from `sites`: the powiat code and the representative point.
# Parcel checks have neither, so they do not select it.
_PLACE_SOURCE = "powiat AS powiat_teryt, ST_PointOnSurface(geom) AS pt"

# Place names for rows already narrowed to one page (alias `p`: `powiat_teryt` and the point `pt`).
# The powiat and voivodeship come from the site's powiat code (the search keeps a parcel by its representative
# point, so it is the point's powiat); only the gmina needs the spatial join, the same one coverage.py uses.
_PLACE_COLUMNS = "g.name AS gmina, pw.name AS powiat, v.name AS voivodeship"
_PLACE_JOINS = """
    LEFT JOIN admin_units g ON g.level = 'gmina' AND ST_Covers(g.geom, p.pt)
    LEFT JOIN admin_units pw ON pw.level = 'powiat' AND pw.teryt = p.powiat_teryt
    LEFT JOIN admin_units v ON v.level = 'voivodeship' AND v.teryt = left(p.powiat_teryt, 2)
"""


# Metres of margin around the site in the card's map block and its orthophoto.
MAP_MARGIN_M = 200.0


def _unavailable() -> HTTPException:
    return HTTPException(503, "Baza danych jest niedostępna.")


@router.get("/sites", response_model=SitesPage)
def list_sites(
    session: Annotated[Session, Depends(db_session)],
    sort: Annotated[Literal["score", "upside", "completeness", "area", "mw"], Query()] = "score",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    min_mw: Annotated[float, Query(ge=0)] = 0,
    min_ha: Annotated[float, Query(ge=0)] = 0,
    include_excluded: bool = False,
    powiat: Annotated[str | None, Query(pattern=r"^\d{4}$")] = None,
    voivodeship: Annotated[str | None, Query(pattern=r"^\d{2}$")] = None,
    profile: Annotated[Literal["built", "container"] | None, Query()] = None,
    hide_red: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> SitesPage:
    where = "WHERE estimated_mw >= :min_mw AND area_ha >= :min_ha AND (:include_excluded OR NOT excluded)"
    params: dict[str, Any] = {"min_mw": min_mw, "min_ha": min_ha, "include_excluded": include_excluded}
    if powiat is not None:
        where += " AND powiat = :powiat"
        params["powiat"] = powiat
    if voivodeship is not None:
        where += " AND left(powiat, 2) = :voivodeship"
        params["voivodeship"] = voivodeship
    if hide_red:
        if profile is None:
            raise HTTPException(422, "Ukrywanie odrzuconych wymaga wybranego profilu.")
        # `profile` is a Literal, so the column name never comes from the query string.
        where += f" AND verdict_{profile} IS DISTINCT FROM 'red'"
    column = SORT_COLUMNS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    # Keyset: the rows strictly after the cursor's row in this order; site_id (always ascending) breaks ties.
    # The count keeps the base filter, so `total` does not depend on the page.
    keyset = ""
    if cursor is not None:
        if offset:
            raise HTTPException(422, "Nie łącz kursora z przesunięciem (offset).")
        try:
            after = decode_cursor(cursor, sort, order)
        except CursorError as exc:
            raise HTTPException(422, str(exc)) from exc
        comparison = ">" if order == "asc" else "<"
        keyset = (
            f" AND ({column} {comparison} :cursor_value"
            f" OR ({column} = :cursor_value AND site_id > :cursor_site_id))"
        )
        params |= {"cursor_value": after.value, "cursor_site_id": after.site_id}
    try:
        total = session.execute(text(f"SELECT count(*) FROM sites {where}"), params).scalar_one()
        rows = session.execute(
            text(
                f"SELECT p.*, {_PLACE_COLUMNS} FROM ("
                f"SELECT {_SUMMARY_COLUMNS}, {_VERDICT_COLUMNS}, {_PLACE_SOURCE} FROM sites {where}{keyset} "
                f"ORDER BY {column} {direction}, site_id LIMIT :limit OFFSET :offset"
                f") p {_PLACE_JOINS} ORDER BY p.{column} {direction}, p.site_id"
            ),
            # One extra row tells whether a next page exists without a second count.
            {**params, "limit": limit + 1, "offset": offset},
        ).mappings()
        fetched = [dict(row) for row in rows]
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    page = fetched[:limit]
    next_cursor = None
    if len(fetched) > limit:
        last = page[-1]
        next_cursor = encode_cursor(sort, order, float(last[column]), last["site_id"])
    items = [_summary(row) for row in page]
    return SitesPage(total=total, offset=offset, limit=limit, items=items, next_cursor=next_cursor)


def _mw_fact(row: Mapping[str, Any]) -> FactOut:
    """The estimate is area × density (config): source is the parcel area, confidence is estimated.

    `as_of` is the scoring run date, since the parcel data's own date is not stored yet.
    """
    return FactOut(
        value=row["estimated_mw"], source_id="egib", as_of=row["scored_at"], confidence="estimated"
    )


def _summary(row: Mapping[str, Any]) -> SiteSummary:
    # Parcel checks are not placed in a gmina: their rows carry no place columns.
    place_fields = ("gmina", "powiat", "voivodeship")
    derived = ("mw_fact", "profile_categories")
    fields = {
        k: row.get(k) if k in place_fields else row[k] for k in SiteSummary.model_fields if k not in derived
    }
    built, container = row.get("verdict_built"), row.get("verdict_container")
    categories = {"built": built, "container": container} if built and container else None
    return SiteSummary(**fields, mw_fact=_mw_fact(row), profile_categories=categories)


def _fact_map(features: dict[str, Any]) -> dict[str, FactOut]:
    return {k: FactOut(**v) for k, v in features.items() if v is not None}


@router.get("/sites/{site_id}", response_model=SiteDetail)
def get_site(site_id: str, session: Annotated[Session, Depends(db_session)]) -> SiteDetail:
    row = _site_row(session, site_id)
    if row is None:
        raise HTTPException(404, "Nie ma takiej lokalizacji.")
    try:
        return _site_detail(row)
    except (KeyError, TypeError, ValueError) as exc:
        # A row the scorer did not write as expected: log the details, never send them to the client.
        logger.exception("Malformed sites row %s", site_id)
        raise HTTPException(500, "Nie udało się wczytać danych tej lokalizacji.") from exc


def _site_row(session: Session, site_id: str) -> RowMapping | None:
    try:
        row = (
            session.execute(
                text(
                    f"SELECT p.*, {_PLACE_COLUMNS} FROM ("
                    f"SELECT {_SUMMARY_COLUMNS}, {_VERDICT_COLUMNS}, profiles, {_PLACE_SOURCE}, geom, "
                    "parcel_ids, detail, "
                    "ST_AsGeoJSON(ST_Transform(geom, 4326)) AS geojson FROM sites WHERE site_id = :site_id"
                    f") p {_PLACE_JOINS}"
                ),
                {"site_id": site_id},
            )
            .mappings()
            .first()
        )
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    return row


def _site_detail(row: RowMapping) -> SiteDetail:
    detail = row["detail"]
    score = detail["score"]
    return SiteDetail(
        **_summary(dict(row)).model_dump(),
        parcel_ids=row["parcel_ids"],
        scored_at=row["scored_at"],
        exclusions=score["exclusions"],
        categories=score["categories"],
        items=score["items"],
        features=_fact_map(detail["features"]),
        geometry=json.loads(row["geojson"]),
        map=SiteMapOut(**build_sheet(wkb.loads(row["geom"], hex=True), [], margin_m=MAP_MARGIN_M)),
        profiles=row.get("profiles"),
    )


@router.get(
    "/sites/{site_id}/orthophoto",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def get_site_orthophoto(site_id: str, session: Annotated[Session, Depends(db_session)]) -> Response:
    """The Geoportal orthophoto for exactly the card map's box, asked for only when the card shows it."""
    try:
        geom = session.execute(
            text("SELECT geom FROM sites WHERE site_id = :site_id"), {"site_id": site_id}
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    if geom is None:
        raise HTTPException(404, "Nie ma takiej lokalizacji.")
    sheet = build_sheet(wkb.loads(geom, hex=True), [], margin_m=MAP_MARGIN_M)
    try:
        wanted = request_for_sheet(tuple(sheet["origin_2180"]), tuple(sheet["bbox_m"]))
    except ValueError as exc:
        raise HTTPException(404, "Ta lokalizacja jest za duża na ortofotomapę.") from exc
    settings = get_settings()
    _, cache = build_client(settings)
    try:
        picture = fetch_orthophoto(wanted, user_agent=settings.user_agent, cache=cache)
    except OrthophotoUnavailable as exc:
        raise HTTPException(
            502, "Geoportal nie zwrócił ortofotomapy dla tego miejsca. Spróbuj za chwilę."
        ) from exc
    return Response(picture, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
