"""Powiats per voivodeship: which have a successful search, and which the planner should search first."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.security import current_user
from dclh_pipeline.db.models import PowiatPriorityRow, UserRow
from dclh_pipeline.powiat_search import pick_powiats, powiats_outside_aoi, queued_powiats

router = APIRouter(tags=["powiats"], dependencies=[Depends(current_user)])

ERROR_CHARS = 300  # same cut as the Zadania page: the exception and its first detail
NEXT_UP = 4  # how many upcoming powiats the tab shows; the nightly run takes more (`limit` in infra/crontab)


class PowiatItem(BaseModel):
    teryt: str
    name: str
    voivodeship_teryt: str | None
    voivodeship_name: str | None
    # Checked = the powiat has a successful search. A failed latest attempt does not undo that.
    checked: bool
    last_ok_at: datetime | None
    last_attempt_at: datetime | None
    status: str | None
    sites: int | None
    error: str | None
    priority: bool
    # The powiat reaches the stored AOI. The nightly plan skips the ones where this is false.
    in_aoi: bool


class PowiatList(BaseModel):
    items: list[PowiatItem]
    # The powiats the next nightly run would search first, from the saved priority list (not unsaved changes).
    next_up: list[str]


class PriorityIn(BaseModel):
    teryts: list[str]


_POWIATS = text(
    """
    SELECT p.teryt, p.name, p.parent_teryt AS voivodeship_teryt, v.name AS voivodeship_name,
           r.last_ok_at IS NOT NULL AS checked, r.last_ok_at, r.last_attempt_at, r.status, r.sites,
           left(r.error, :chars) AS error, pr.teryt IS NOT NULL AS priority
    FROM admin_units p
    LEFT JOIN admin_units v ON v.level = 'voivodeship' AND v.teryt = p.parent_teryt
    LEFT JOIN search_runs r ON r.teryt = p.teryt
    LEFT JOIN powiat_priority pr ON pr.teryt = p.teryt
    WHERE p.level = 'powiat'
    ORDER BY v.name, p.name, p.teryt
    """
)


def _next_up(session: Session, items: list[PowiatItem]) -> list[str]:
    """The planner's own choice (`pick_powiats`), so the preview cannot drift from the nightly run."""
    busy = queued_powiats(session) | {i.teryt for i in items if not i.in_aoi}
    saved = session.scalars(
        select(PowiatPriorityRow.teryt).order_by(PowiatPriorityRow.added_at, PowiatPriorityRow.teryt)
    )
    return pick_powiats(
        [i.teryt for i in items if i.teryt not in busy],
        {i.teryt: i.last_attempt_at for i in items if i.last_attempt_at is not None},
        NEXT_UP,
        priority=list(saved),
        searched_ok={i.teryt for i in items if i.checked},
    )


def _list(session: Session) -> PowiatList:
    rows = session.execute(_POWIATS, {"chars": ERROR_CHARS}).mappings()
    outside = powiats_outside_aoi(session)
    items = [PowiatItem(**row, in_aoi=row["teryt"] not in outside) for row in rows]
    return PowiatList(items=items, next_up=_next_up(session, items))


@router.get("/powiats", response_model=PowiatList)
def list_powiats(session: Annotated[Session, Depends(db_session)]) -> PowiatList:
    return _list(session)


@router.put("/powiats/priority", response_model=PowiatList)
def save_priority(
    body: PriorityIn,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> PowiatList:
    """Replace the whole priority list (the Save button). Codes that stay keep their `added_at`, so the
    planner's order of the list does not change."""
    wanted = list(dict.fromkeys(body.teryts))
    known = set(
        session.execute(
            text("SELECT teryt FROM admin_units WHERE level = 'powiat' AND teryt = ANY(:codes)"),
            {"codes": wanted},
        ).scalars()
    )
    unknown = [t for t in wanted if t not in known]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Nieznane powiaty: {', '.join(unknown)}")
    session.execute(text("DELETE FROM powiat_priority WHERE NOT (teryt = ANY(:codes))"), {"codes": wanted})
    have = set(session.execute(text("SELECT teryt FROM powiat_priority")).scalars())
    session.add_all(PowiatPriorityRow(teryt=t, added_by=user.email) for t in wanted if t not in have)
    session.commit()
    return _list(session)
