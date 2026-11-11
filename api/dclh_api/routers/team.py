"""Claude access for the team, for admins (PLAN.md, "Claude tab").

The same switch as `dclh user mcp-access`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.security import admin_user
from dclh_pipeline.oauth_admin import set_mcp_access

router = APIRouter(prefix="/team", tags=["team"], dependencies=[Depends(admin_user)])

NO_USER = "Nie ma takiego użytkownika."

Email = Annotated[str, Path(min_length=3, max_length=320)]


class TeamMember(BaseModel):
    email: str
    role: str
    mcp_access: bool
    # Apps (Claude on the web, Claude Code, ...) that hold a token for this user.
    connections: int


class AccessIn(BaseModel):
    on: bool


_MEMBERS = (
    "SELECT u.email, u.role, u.mcp_access, count(DISTINCT t.client_id) AS connections "
    "FROM users u LEFT JOIN oauth_tokens t ON t.user_id = u.id "
    "{where} GROUP BY u.id ORDER BY u.email"
)


def _members(session: Session, email: str | None = None) -> list[TeamMember]:
    where = "WHERE u.email = :e" if email is not None else ""
    rows = session.execute(text(_MEMBERS.format(where=where)), {"e": email}).mappings()
    return [TeamMember(**row) for row in rows]


@router.get("", response_model=list[TeamMember])
def list_team(session: Annotated[Session, Depends(db_session)]) -> list[TeamMember]:
    return _members(session)


@router.put("/{email}/mcp-access", response_model=TeamMember)
def switch_access(
    email: Email, body: AccessIn, session: Annotated[Session, Depends(db_session)]
) -> TeamMember:
    """Off also drops the user's tokens and codes, so Claude is signed out at once."""
    try:
        user = set_mcp_access(session, email, body.on)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_USER) from exc
    session.commit()
    return _members(session, user.email)[0]
