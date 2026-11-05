"""What every MCP tool needs: a database session, the user behind the token, and the shared wording."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import anyio.to_thread
from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session, sessionmaker

from dclh_pipeline.db import get_engine
from dclh_pipeline.db.models import UserRow

# Appended to every tool result that carries grid information, so Claude cannot present a proxy as a fact.
GRID_NOTE = (
    "Ocena sieci to szacunek z odległości do sieci i danych publicznych operatorów, a nie potwierdzona "
    "moc przyłączeniowa. Wiążą tylko warunki przyłączenia wydane przez OSD."
)


def site_url(public_url: str, site_id: str) -> str:
    return f"{public_url.rstrip('/')}/lokalizacje/{site_id}"


@contextmanager
def tool_session() -> Iterator[Session]:
    session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def tool_user(session: Session) -> UserRow:
    """The user the access token was issued to. The token class (Task 2) carries `user_id`."""
    token = get_access_token()
    user_id = getattr(token, "user_id", None)
    if not isinstance(user_id, int):
        raise ToolError("Brak zalogowanego użytkownika. Połącz Claude z aplikacją od nowa.")
    user = session.get(UserRow, user_id)
    if user is None:
        raise ToolError("Konto użytkownika nie istnieje. Połącz Claude z aplikacją od nowa.")
    return user


def _run[T](fn: Callable[[Session, UserRow], T]) -> T:
    with tool_session() as session:
        user = tool_user(session)
        try:
            return fn(session, user)
        except HTTPException as exc:
            # The routers' messages are already Polish and meant for a person; Claude passes them on.
            raise ToolError(str(exc.detail)) from exc


async def run_db[T](fn: Callable[[Session, UserRow], T]) -> T:
    """Runs `fn(session, user)` in a worker thread: the routers block, and the event loop is shared."""
    return await anyio.to_thread.run_sync(_run, fn)
