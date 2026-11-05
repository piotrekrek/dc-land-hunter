"""Consent step and connection list of the Claude connector (PLAN.md, "Claude connector (MCP)", Task 3).

Claude's `/authorize` request lands on the web page `/polacz-claude?req=...` (see `mcp/oauth.py`). The page is
reachable only for a logged-in user, shows what Claude asks for, and posts the decision here. The answer is
the redirect URL (with the code) that the page then navigates to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.mcp import oauth as store
from dclh_api.security import current_user
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.settings import get_settings

router = APIRouter(prefix="/oauth", tags=["oauth"], dependencies=[Depends(current_user)])

REQUEST_GONE = "Prośba o połączenie wygasła lub została już użyta. Zacznij łączenie w Claude od nowa."
NO_ACCESS = "Twoje konto nie ma włączonego dostępu dla Claude. Poproś administratora."
UNNAMED_CLIENT = "Aplikacja bez nazwy"

# What each scope lets Claude do, as the consent page words it ("Claude będzie mógł: ...").
SCOPE_LABELS = {
    "dclh.read": "czytać lokalizacje, oceny, pokrycie powiatów i etapy projektów (bez notatek)",
    "dclh.write": "obserwować lokalizacje i zlecać sprawdzenie działki",
}

ReqId = Annotated[str, Path(min_length=1, max_length=128)]


class ScopeOut(BaseModel):
    id: str
    label: str


class ConsentInfo(BaseModel):
    client_name: str
    scopes: list[ScopeOut]
    user_email: str
    # False when no admin switched Claude access on for the account: the page explains instead of asking.
    can_connect: bool


class DecisionIn(BaseModel):
    approve: bool


class DecisionOut(BaseModel):
    redirect_to: str


class ConnectionOut(BaseModel):
    client_id: str
    client_name: str
    connected_at: datetime


class ConnectionList(BaseModel):
    # The address to give Claude (DCLH_PUBLIC_URL + /mcp); null when the connector is off on this server.
    connector_url: str | None
    items: list[ConnectionOut]


@router.get("/requests/{req_id}", response_model=ConsentInfo)
def get_request(
    req_id: ReqId,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> ConsentInfo:
    pending = store.pending_request(session, req_id)
    if pending is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, REQUEST_GONE)
    return ConsentInfo(
        client_name=pending.client_name or UNNAMED_CLIENT,
        scopes=[ScopeOut(id=s, label=SCOPE_LABELS.get(s, s)) for s in pending.scopes],
        user_email=user.email,
        can_connect=bool(user.mcp_access),
    )


@router.post("/requests/{req_id}/decision", response_model=DecisionOut)
def decide(
    req_id: ReqId,
    body: DecisionIn,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> DecisionOut:
    """Approving needs `mcp_access`; refusing never does. A request can be decided once."""
    try:
        if body.approve:
            redirect = store.approve_request(session, req_id, user)
        else:
            redirect = store.deny_request(session, req_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, REQUEST_GONE) from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NO_ACCESS) from exc
    session.commit()
    return DecisionOut(redirect_to=redirect)


@router.get("/connections", response_model=ConnectionList)
def list_connections(
    session: Annotated[Session, Depends(db_session)], user: Annotated[UserRow, Depends(current_user)]
) -> ConnectionList:
    """The apps this user connected (one per client that holds a token), newest first."""
    rows = session.execute(
        text(
            "SELECT c.client_id, c.info->>'client_name' AS client_name, min(t.created_at) AS connected_at "
            "FROM oauth_tokens t JOIN oauth_clients c ON c.client_id = t.client_id "
            "WHERE t.user_id = :u GROUP BY c.client_id, c.info->>'client_name' ORDER BY connected_at DESC"
        ),
        {"u": user.id},
    ).mappings()
    public_url = get_settings().public_url
    return ConnectionList(
        connector_url=f"{public_url}/mcp" if public_url else None,
        items=[
            ConnectionOut(
                client_id=r["client_id"],
                client_name=r["client_name"] or UNNAMED_CLIENT,
                connected_at=r["connected_at"],
            )
            for r in rows
        ],
    )


@router.delete("/connections/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    client_id: ReqId,
    session: Annotated[Session, Depends(db_session)],
    user: Annotated[UserRow, Depends(current_user)],
) -> Response:
    """Drops this user's tokens for the client. Idempotent; never touches another user's connection."""
    store.revoke_connection(session, client_id, user.id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
