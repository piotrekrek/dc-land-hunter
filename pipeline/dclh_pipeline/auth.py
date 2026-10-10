"""Password hashing, session tokens and user management (brief section 9.3).

Passwords use argon2id. Session tokens are random 256-bit values; only their SHA-256 is stored, so a leaked
database does not leak usable sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from dclh_pipeline.db.models import SessionRow, UserRow

Role = Literal["admin", "member"]

MIN_PASSWORD_LENGTH = 12
SESSION_TTL = timedelta(days=14)

_hasher = PasswordHasher()
# Verified against when the email is unknown, so timing does not reveal which emails exist.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


class PasswordTooShortError(ValueError):
    pass


class UserExistsError(ValueError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_user(session: Session, email: str, password: str, role: Role = "member") -> UserRow:
    email = normalize_email(email)
    if "@" not in email:
        raise ValueError("invalid email")
    if session.scalar(select(UserRow).where(UserRow.email == email)) is not None:
        raise UserExistsError(email)
    user = UserRow(email=email, password_hash=hash_password(password), role=role)
    session.add(user)
    session.flush()
    return user


def set_password(session: Session, email: str, password: str) -> UserRow:
    user = session.scalar(select(UserRow).where(UserRow.email == normalize_email(email)))
    if user is None:
        raise LookupError(email)
    user.password_hash = hash_password(password)
    # A password change signs the user out everywhere.
    session.execute(delete(SessionRow).where(SessionRow.user_id == user.id))
    return user


def authenticate(session: Session, email: str, password: str) -> UserRow | None:
    user = session.scalar(select(UserRow).where(UserRow.email == normalize_email(email)))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(session: Session, user: UserRow, *, now: datetime | None = None) -> tuple[str, datetime]:
    """Create a login session. Returns the raw token (for the cookie) and its expiry."""
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    expires = now + SESSION_TTL
    session.add(
        SessionRow(token_hash=_token_hash(token), user_id=user.id, created_at=now, expires_at=expires)
    )
    return token, expires


def user_for_token(session: Session, token: str | None, *, now: datetime | None = None) -> UserRow | None:
    if not token:
        return None
    now = now or datetime.now(UTC)
    row = session.get(SessionRow, _token_hash(token))
    if row is None or row.expires_at <= now:
        return None
    return session.get(UserRow, row.user_id)


def delete_session(session: Session, token: str | None) -> None:
    if token:
        session.execute(delete(SessionRow).where(SessionRow.token_hash == _token_hash(token)))


def purge_expired_sessions(session: Session, *, now: datetime | None = None) -> int:
    result = session.execute(delete(SessionRow).where(SessionRow.expires_at <= (now or datetime.now(UTC))))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
