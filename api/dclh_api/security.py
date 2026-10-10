"""Session cookie, current-user dependencies and login throttling."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_pipeline.auth import user_for_token
from dclh_pipeline.db.models import UserRow

COOKIE_NAME = "dclh_session"


def get_token(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def current_user(
    session: Annotated[Session, Depends(db_session)],
    token: Annotated[str | None, Depends(get_token)],
) -> UserRow:
    user = user_for_token(session, token)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wymagane logowanie")
    return user


def admin_user(user: Annotated[UserRow, Depends(current_user)]) -> UserRow:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wymagane uprawnienia administratora")
    return user


class LoginThrottle:
    """Blocks an (email, client) pair after too many failures. In memory: fine for one API process."""

    def __init__(
        self,
        max_failures: int = 5,
        window_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        q = self._failures[key]
        cutoff = self._clock() - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._recent(key).append(self._clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


login_throttle = LoginThrottle()
