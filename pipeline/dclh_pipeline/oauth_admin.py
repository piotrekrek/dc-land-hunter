"""Admin side of the Claude connector: who may connect, what is connected, and the daily clean-up.

The OAuth protocol itself lives in `dclh_api.mcp.oauth`. These functions only need the tables, so the CLI
and the scheduler (which run from the pipeline package) can use them without importing the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dclh_pipeline.auth import normalize_email
from dclh_pipeline.db.models import UserRow

CLIENT_RETENTION = timedelta(days=7)  # a registered client that holds no token is dropped after this


@dataclass(frozen=True)
class Connection:
    email: str
    client_name: str | None
    client_id: str
    connected_at: datetime
    expires_at: datetime  # when the longest-lived token (the refresh token) stops working


def set_mcp_access(session: Session, email: str, on: bool) -> UserRow:
    """Switches Claude access for a user. Switching it off also drops the user's tokens and pending codes.

    The token lookup already refuses a user without access, so the deletes are for tidiness: after a later
    `--on` the user connects Claude again from a clean slate.
    """
    user = session.scalar(select(UserRow).where(UserRow.email == normalize_email(email)))
    if user is None:
        raise LookupError(email)
    user.mcp_access = on
    if not on:
        for table in ("oauth_tokens", "oauth_codes"):
            session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user.id})
    return user


def list_connections(session: Session) -> list[Connection]:
    rows = session.execute(
        text(
            "SELECT u.email, c.info->>'client_name' AS client_name, c.client_id, "
            "min(t.created_at) AS connected_at, max(t.expires_at) AS expires_at "
            "FROM oauth_tokens t JOIN oauth_clients c ON c.client_id = t.client_id "
            "JOIN users u ON u.id = t.user_id "
            "GROUP BY u.email, c.client_id, c.info->>'client_name' ORDER BY u.email, connected_at"
        )
    ).mappings()
    return [Connection(**row) for row in rows]


def purge_oauth(session: Session, now: datetime) -> int:
    """Removes what has expired, and clients that hold no token a week after registering."""
    statements: list[tuple[str, dict[str, Any]]] = [
        (f"DELETE FROM {table} WHERE expires_at <= :now", {"now": now})
        for table in ("oauth_requests", "oauth_codes", "oauth_tokens")
    ]
    statements.append(
        (
            "DELETE FROM oauth_clients c WHERE c.created_at <= :old "
            "AND NOT EXISTS (SELECT 1 FROM oauth_tokens t WHERE t.client_id = c.client_id)",
            {"old": now - CLIENT_RETENTION},
        )
    )
    removed = 0
    for sql, args in statements:
        result = session.execute(text(sql), args)
        # `rowcount` is on the cursor result of a DELETE, but not on the generic Result type (as in auth.py).
        removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
    return removed
