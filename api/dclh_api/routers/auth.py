from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.security import COOKIE_NAME, current_user, get_token, login_throttle
from dclh_pipeline.auth import authenticate, create_session, delete_session, normalize_email
from dclh_pipeline.db.models import UserRow

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


class UserOut(BaseModel):
    email: str
    role: str


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{normalize_email(email)}|{host}"


@router.post("/login", response_model=UserOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(db_session)],
) -> UserOut:
    key = _client_key(request, body.email)
    if login_throttle.blocked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Zbyt wiele prób logowania. Spróbuj za kilka minut."
        )
    user = authenticate(session, body.email, body.password)
    if user is None:
        login_throttle.record_failure(key)
        session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nieprawidłowy e-mail lub hasło")
    login_throttle.reset(key)
    token, expires = create_session(session, user)
    session.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        expires=expires,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return UserOut(email=user.email, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    session: Annotated[Session, Depends(db_session)],
    token: Annotated[str | None, Depends(get_token)],
) -> None:
    delete_session(session, token)
    session.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
def me(user: Annotated[UserRow, Depends(current_user)]) -> UserOut:
    return UserOut(email=user.email, role=user.role)
