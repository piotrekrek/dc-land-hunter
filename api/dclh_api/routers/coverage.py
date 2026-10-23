"""Data coverage: the share of kept sites whose score items have data, per voivodeship, powiat or gmina.

A site belongs to the area holding its representative point (`ST_PointOnSurface`), the same point the search
uses to keep a parcel. Areas with no kept site are left out: there is nothing to cover.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.schemas import CoverageArea, CoverageOut
from dclh_api.security import current_user

router = APIRouter(tags=["coverage"], dependencies=[Depends(current_user)])

# API level -> `admin_units.level`
LEVELS: dict[str, str] = {"wojewodztwo": "voivodeship", "powiat": "powiat", "gmina": "gmina"}
# The score items the view tracks, by their key in score.items[]; the labels live in the web app.
TRACKED_ITEMS = ("zoning", "pen_soil_iii", "bonus_psi", "grid_headroom")
ItemKey = Literal["all", "zoning", "pen_soil_iii", "bonus_psi", "grid_headroom"]
# Metres in EPSG:2180: invisible at gmina scale, and it keeps the response small.
SIMPLIFY_M = 20

_SITE_COUNTS = text(
    """
    SELECT a.teryt, a.name, count(*) AS sites
    FROM sites s
    JOIN admin_units a ON a.level = :level AND ST_Covers(a.geom, ST_PointOnSurface(s.geom))
    WHERE NOT s.excluded
    GROUP BY a.teryt, a.name
    """
)

# One row per area and item key. A site without items gives one row with a NULL key, which is skipped later.
_ITEM_COUNTS = text(
    """
    SELECT a.teryt, it->>'key' AS key,
           count(DISTINCT s.site_id) FILTER (WHERE it->>'confidence' <> 'missing') AS with_data,
           count(it) AS entries,
           count(it) FILTER (WHERE it->>'confidence' <> 'missing') AS known
    FROM sites s
    JOIN admin_units a ON a.level = :level AND ST_Covers(a.geom, ST_PointOnSurface(s.geom))
    LEFT JOIN LATERAL jsonb_array_elements(s.detail->'score'->'items') AS it ON true
    WHERE NOT s.excluded
    GROUP BY a.teryt, it->>'key'
    """
)

_GEOMETRY = text(
    """
    SELECT teryt,
           ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, :tolerance), 4326), 5) AS geojson
    FROM admin_units
    WHERE level = :level AND teryt = ANY(:teryts)
    """
)


def _unavailable() -> HTTPException:
    return HTTPException(503, "Baza danych jest niedostępna.")


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def build_areas(
    site_rows: Sequence[Mapping[str, Any]],
    item_rows: Sequence[Mapping[str, Any]],
    item: str,
    geometries: Mapping[str, dict[str, Any]] | None = None,
) -> list[CoverageArea]:
    """Pure assembly from the two aggregate queries; `item` picks the headline share (see CoverageArea)."""
    known_total: dict[str, int] = defaultdict(int)
    entry_total: dict[str, int] = defaultdict(int)
    with_data: dict[tuple[str, str], int] = {}
    for row in item_rows:
        if row["key"] is None:
            continue
        known_total[row["teryt"]] += row["known"]
        entry_total[row["teryt"]] += row["entries"]
        with_data[(row["teryt"], row["key"])] = row["with_data"]

    areas: list[CoverageArea] = []
    for row in sorted(site_rows, key=lambda r: r["teryt"]):
        teryt, sites = row["teryt"], row["sites"]
        by_item = {key: _share(with_data.get((teryt, key), 0), sites) for key in TRACKED_ITEMS}
        if item == "all":
            done, total = known_total[teryt], entry_total[teryt]
        else:
            done, total = with_data.get((teryt, item), 0), sites
        areas.append(
            CoverageArea(
                teryt=teryt,
                name=row["name"],
                sites=sites,
                with_data=done,
                total=total,
                share=_share(done, total),
                by_item=by_item,
                geometry=(geometries or {}).get(teryt),
            )
        )
    return areas


@router.get("/coverage", response_model=CoverageOut)
def get_coverage(
    session: Annotated[Session, Depends(db_session)],
    level: Annotated[Literal["wojewodztwo", "powiat", "gmina"], Query()] = "powiat",
    item: Annotated[ItemKey, Query()] = "all",
    include_geometry: bool = False,
) -> CoverageOut:
    db_level = LEVELS[level]
    params: dict[str, Any] = {"level": db_level}
    try:
        site_rows = [dict(r) for r in session.execute(_SITE_COUNTS, params).mappings()]
        item_rows = [dict(r) for r in session.execute(_ITEM_COUNTS, params).mappings()]
        geometries: dict[str, dict[str, Any]] | None = None
        if include_geometry and site_rows:
            shapes = session.execute(
                _GEOMETRY,
                {**params, "tolerance": SIMPLIFY_M, "teryts": [r["teryt"] for r in site_rows]},
            ).mappings()
            geometries = {r["teryt"]: json.loads(r["geojson"]) for r in shapes}
    except SQLAlchemyError as exc:
        raise _unavailable() from exc
    return CoverageOut(
        level=level,
        item=item,
        areas=build_areas(site_rows, item_rows, item, geometries),
    )
