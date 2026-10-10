"""Ranked candidate sites: the list (filters, sort, pages) and the site card (brief sections 8.1, 6)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.schemas import FactOut, SiteDetail, SitesPage, SiteSummary
from dclh_api.security import current_user

router = APIRouter(tags=["sites"], dependencies=[Depends(current_user)])

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
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SitesPage:
    where = "WHERE estimated_mw >= :min_mw AND area_ha >= :min_ha AND (:include_excluded OR NOT excluded)"
    params: dict[str, Any] = {"min_mw": min_mw, "min_ha": min_ha, "include_excluded": include_excluded}
    column = SORT_COLUMNS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    try:
        total = session.execute(text(f"SELECT count(*) FROM sites {where}"), params).scalar_one()
        rows = session.execute(
            text(
                f"SELECT {_SUMMARY_COLUMNS} FROM sites {where} "
                f"ORDER BY {column} {direction}, site_id LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        ).mappings()
        items = [_summary(dict(row)) for row in rows]
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    return SitesPage(total=total, offset=offset, limit=limit, items=items)


def _mw_fact(row: Mapping[str, Any]) -> FactOut:
    """The estimate is area × density (config): source is the parcel area, confidence is estimated.

    `as_of` is the scoring run date, since the parcel data's own date is not stored yet.
    """
    return FactOut(
        value=row["estimated_mw"], source_id="egib", as_of=row["scored_at"], confidence="estimated"
    )


def _summary(row: Mapping[str, Any]) -> SiteSummary:
    fields = {k: row[k] for k in SiteSummary.model_fields if k != "mw_fact"}
    return SiteSummary(**fields, mw_fact=_mw_fact(row))


def _fact_map(features: dict[str, Any]) -> dict[str, FactOut]:
    return {k: FactOut(**v) for k, v in features.items() if v is not None}


@router.get("/sites/{site_id}", response_model=SiteDetail)
def get_site(site_id: str, session: Annotated[Session, Depends(db_session)]) -> SiteDetail:
    try:
        row = (
            session.execute(
                text(
                    f"SELECT {_SUMMARY_COLUMNS}, parcel_ids, scored_at, detail, "
                    "ST_AsGeoJSON(ST_Transform(geom, 4326)) AS geojson FROM sites WHERE site_id = :site_id"
                ),
                {"site_id": site_id},
            )
            .mappings()
            .first()
        )
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    if row is None:
        raise HTTPException(404, "Nie ma takiej lokalizacji.")
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
    )
