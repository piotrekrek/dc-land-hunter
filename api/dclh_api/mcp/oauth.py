"""OAuth 2.1 authorization server for the Claude connector, stored in Postgres (PLAN.md, Task 2).

The MCP SDK runs the protocol (PKCE, redirect URI equality, code expiry, client authentication). This
module is the storage and the rules the SDK leaves to a provider: who may register, single-use codes, refresh
rotation, and the link between a token and an app user. Nothing lives in memory, so an API restart breaks
nothing.

The logic is in plain functions that take a `Session` (the caller commits); `DclhOAuthProvider` wraps them in
the SDK's async interface and runs each call in a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import anyio.to_thread
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.db.models import UserRow

SCOPES = ["dclh.read", "dclh.write"]

REQUEST_TTL = timedelta(minutes=10)  # the consent step: from /authorize to the user's click
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
MAX_CLIENTS = 200  # anyone can register a client; this keeps the table small (a client alone grants nothing)

# Claude's own callback (Anthropic: it may move from claude.ai to claude.com, so both are allowed) and the
# loopback addresses that Claude Code and the MCP Inspector use, on any port.
CLAUDE_HOSTS = frozenset({"claude.ai", "claude.com"})
CLAUDE_CALLBACK_PATH = "/api/mcp/auth_callback"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class DclhAuthorizationCode(AuthorizationCode):
    user_id: int


class DclhRefreshToken(RefreshToken):
    user_id: int


class DclhAccessToken(AccessToken):
    user_id: int


@dataclass(frozen=True)
class PendingRequest:
    """What the consent page shows about a request that waits for the user's decision."""

    client_name: str | None
    scopes: list[str]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def redirect_uri_allowed(uri: str) -> bool:
    """Only Claude's callback (https, exact path) or a loopback address (http, any port) may receive a code.

    A client that registers any other redirect could phish an authorization code, so registration refuses it.
    """
    try:
        parts = urlparse(uri)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False
    if host is None or parts.username or parts.password or parts.fragment:
        return False
    if parts.scheme == "https":
        on_claude = host in CLAUDE_HOSTS and port is None and parts.path == CLAUDE_CALLBACK_PATH
        return on_claude and not parts.query
    return parts.scheme == "http" and host in LOOPBACK_HOSTS


def register_client(session: Session, info: OAuthClientInformationFull) -> None:
    for uri in info.redirect_uris or []:
        if not redirect_uri_allowed(str(uri)):
            raise RegistrationError("invalid_redirect_uri", "This redirect URI is not allowed")
    if session.execute(text("SELECT count(*) FROM oauth_clients")).scalar_one() >= MAX_CLIENTS:
        raise RegistrationError("invalid_client_metadata", "Too many registered clients")
    session.execute(
        text("INSERT INTO oauth_clients (client_id, info) VALUES (:id, CAST(:info AS jsonb))"),
        {"id": info.client_id, "info": json.dumps(info.model_dump(mode="json"))},
    )


def get_client(session: Session, client_id: str) -> OAuthClientInformationFull | None:
    info = session.execute(
        text("SELECT info FROM oauth_clients WHERE client_id = :id"), {"id": client_id}
    ).scalar_one_or_none()
    return None if info is None else OAuthClientInformationFull.model_validate(info)


def create_request(
    session: Session, public_url: str, client: OAuthClientInformationFull, params: AuthorizationParams
) -> str:
    """Stores the authorization request and returns the consent page URL the browser goes to."""
    if client.client_id is None:
        raise ValueError("a registered client has an id")
    req_id = secrets.token_urlsafe(32)
    scopes = params.scopes or (client.scope or "").split() or SCOPES
    stored = params.model_copy(update={"scopes": scopes}).model_dump(mode="json")
    session.execute(
        text(
            "INSERT INTO oauth_requests (id_hash, client_id, params, expires_at) "
            "VALUES (:h, :c, CAST(:p AS jsonb), :exp)"
        ),
        {
            "h": _hash(req_id),
            "c": client.client_id,
            "p": json.dumps(stored),
            "exp": datetime.now(UTC) + REQUEST_TTL,
        },
    )
    return f"{public_url.rstrip('/')}/polacz-claude?req={req_id}"


def pending_request(session: Session, req_id: str) -> PendingRequest | None:
    row = (
        session.execute(
            text(
                "SELECT c.info->>'client_name' AS client_name, r.params->'scopes' AS scopes "
                "FROM oauth_requests r JOIN oauth_clients c ON c.client_id = r.client_id "
                "WHERE r.id_hash = :h AND r.expires_at > :now"
            ),
            {"h": _hash(req_id), "now": datetime.now(UTC)},
        )
        .mappings()
        .first()
    )
    return None if row is None else PendingRequest(row["client_name"], list(row["scopes"]))


def approve_request(session: Session, req_id: str, user: UserRow) -> str:
    """The user said yes: turns the request into a single-use code and returns the client's redirect URL.

    Raises LookupError for an unknown, expired or already used request, and PermissionError for a user whose
    admin has not switched Claude access on (the request stays, so access can be granted and retried).
    """
    # FOR UPDATE: two clicks on the same request cannot both get a code.
    row = (
        session.execute(
            text(
                "SELECT client_id, params FROM oauth_requests "
                "WHERE id_hash = :h AND expires_at > :now FOR UPDATE"
            ),
            {"h": _hash(req_id), "now": datetime.now(UTC)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError("request not found or expired")
    if not user.mcp_access or user.id is None:
        raise PermissionError("Claude access is not enabled for this user")
    session.execute(text("DELETE FROM oauth_requests WHERE id_hash = :h"), {"h": _hash(req_id)})
    code = secrets.token_urlsafe(32)
    session.execute(
        text(
            "INSERT INTO oauth_codes (code_hash, client_id, user_id, params, expires_at) "
            "VALUES (:h, :c, :u, CAST(:p AS jsonb), :exp)"
        ),
        {
            "h": _hash(code),
            "c": row["client_id"],
            "u": user.id,
            "p": json.dumps(row["params"]),
            "exp": datetime.now(UTC) + CODE_TTL,
        },
    )
    params = AuthorizationParams.model_validate(row["params"])
    return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)


def deny_request(session: Session, req_id: str) -> str:
    """The user said no: drops the request and returns the client's redirect URL with `access_denied`."""
    row = session.execute(
        text("DELETE FROM oauth_requests WHERE id_hash = :h AND expires_at > :now RETURNING params"),
        {"h": _hash(req_id), "now": datetime.now(UTC)},
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("request not found or expired")
    params = AuthorizationParams.model_validate(row)
    return construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)


def load_code(session: Session, client_id: str, code: str) -> DclhAuthorizationCode | None:
    row = (
        session.execute(
            text(
                "SELECT user_id, params, expires_at FROM oauth_codes "
                "WHERE code_hash = :h AND client_id = :c AND expires_at > :now"
            ),
            {"h": _hash(code), "c": client_id, "now": datetime.now(UTC)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    params = AuthorizationParams.model_validate(row["params"])
    return DclhAuthorizationCode(
        code=code,
        scopes=params.scopes or [],
        expires_at=row["expires_at"].timestamp(),
        client_id=client_id,
        code_challenge=params.code_challenge,
        redirect_uri=params.redirect_uri,
        redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        resource=params.resource,
        user_id=row["user_id"],
    )


def _has_access(session: Session, user_id: int) -> bool:
    return bool(
        session.execute(
            text("SELECT mcp_access FROM users WHERE id = :u"), {"u": user_id}
        ).scalar_one_or_none()
    )


def _issue_tokens(
    session: Session, client_id: str, user_id: int, scopes: list[str], resource: str | None
) -> OAuthToken:
    now = datetime.now(UTC)
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for kind, value, ttl in (("access", access, ACCESS_TTL), ("refresh", refresh, REFRESH_TTL)):
        session.execute(
            text(
                "INSERT INTO oauth_tokens "
                "(token_hash, kind, client_id, user_id, scopes, resource, expires_at) "
                "VALUES (:h, :k, :c, :u, :s, :r, :exp)"
            ),
            {
                "h": _hash(value),
                "k": kind,
                "c": client_id,
                "u": user_id,
                "s": scopes,
                "r": resource,
                "exp": now + ttl,
            },
        )
    return OAuthToken(
        access_token=access,
        expires_in=int(ACCESS_TTL.total_seconds()),
        scope=" ".join(scopes),
        refresh_token=refresh,
    )


def exchange_code(session: Session, client_id: str, code: DclhAuthorizationCode) -> OAuthToken:
    # The delete is the single use: of two exchanges of one code, only one gets a row back.
    used = session.execute(
        text("DELETE FROM oauth_codes WHERE code_hash = :h AND client_id = :c RETURNING user_id"),
        {"h": _hash(code.code), "c": client_id},
    ).scalar_one_or_none()
    if used is None:
        raise TokenError("invalid_grant", "authorization code already used")
    if not _has_access(session, code.user_id):
        raise TokenError("invalid_grant", "Claude access is not enabled for this user")
    return _issue_tokens(session, client_id, code.user_id, code.scopes, code.resource)


def load_refresh_token(session: Session, client_id: str, token: str) -> DclhRefreshToken | None:
    row = (
        session.execute(
            text(
                "SELECT user_id, scopes, resource, expires_at FROM oauth_tokens "
                "WHERE token_hash = :h AND kind = 'refresh' AND client_id = :c AND expires_at > :now"
            ),
            {"h": _hash(token), "c": client_id, "now": datetime.now(UTC)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return DclhRefreshToken(
        token=token,
        client_id=client_id,
        scopes=list(row["scopes"]),
        expires_at=int(row["expires_at"].timestamp()),
        resource=row["resource"],
        user_id=row["user_id"],
    )


def exchange_refresh_token(
    session: Session, client_id: str, token: DclhRefreshToken, scopes: list[str]
) -> OAuthToken:
    """Rotates: the used refresh token is deleted, so replaying it fails."""
    used = session.execute(
        text(
            "DELETE FROM oauth_tokens WHERE token_hash = :h AND kind = 'refresh' AND client_id = :c "
            "AND expires_at > :now RETURNING user_id"
        ),
        {"h": _hash(token.token), "c": client_id, "now": datetime.now(UTC)},
    ).scalar_one_or_none()
    if used is None:
        raise TokenError("invalid_grant", "refresh token already used")
    if not _has_access(session, token.user_id):
        raise TokenError("invalid_grant", "Claude access is not enabled for this user")
    return _issue_tokens(session, client_id, token.user_id, scopes, token.resource)


def load_access_token(session: Session, token: str) -> DclhAccessToken | None:
    """The token's user; None when the token is unknown or expired, or the user's access was switched off."""
    row = (
        session.execute(
            text(
                "SELECT t.client_id, t.user_id, t.scopes, t.resource, t.expires_at FROM oauth_tokens t "
                "JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = :h AND t.kind = 'access' AND t.expires_at > :now AND u.mcp_access"
            ),
            {"h": _hash(token), "now": datetime.now(UTC)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return DclhAccessToken(
        token=token,
        client_id=row["client_id"],
        scopes=list(row["scopes"]),
        expires_at=int(row["expires_at"].timestamp()),
        resource=row["resource"],
        user_id=row["user_id"],
    )


def revoke_connection(session: Session, client_id: str, user_id: int) -> None:
    """Drops every token of one connection (one client of one user): access and refresh tokens alike."""
    session.execute(
        text("DELETE FROM oauth_tokens WHERE client_id = :c AND user_id = :u"), {"c": client_id, "u": user_id}
    )


class DclhOAuthProvider:
    """The SDK's `OAuthAuthorizationServerProvider`, backed by the functions above."""

    def __init__(self, public_url: str, session_factory: Callable[[], Session]) -> None:
        self._public_url = public_url
        self._session_factory = session_factory

    def _call[T](self, fn: Callable[[Session], T]) -> T:
        session = self._session_factory()
        try:
            result = fn(session)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def _db[T](self, fn: Callable[[Session], T]) -> T:
        return await anyio.to_thread.run_sync(self._call, fn)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._db(lambda s: get_client(s, client_id))

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._db(lambda s: register_client(s, client_info))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        return await self._db(lambda s: create_request(s, self._public_url, client, params))

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> DclhAuthorizationCode | None:
        client_id = _client_id(client)
        return await self._db(lambda s: load_code(s, client_id, authorization_code))

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: DclhAuthorizationCode
    ) -> OAuthToken:
        client_id = _client_id(client)
        return await self._db(lambda s: exchange_code(s, client_id, authorization_code))

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> DclhRefreshToken | None:
        client_id = _client_id(client)
        return await self._db(lambda s: load_refresh_token(s, client_id, refresh_token))

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: DclhRefreshToken, scopes: list[str]
    ) -> OAuthToken:
        client_id = _client_id(client)
        return await self._db(lambda s: exchange_refresh_token(s, client_id, refresh_token, scopes))

    async def load_access_token(self, token: str) -> DclhAccessToken | None:
        return await self._db(lambda s: load_access_token(s, token))

    async def revoke_token(self, token: DclhAccessToken | DclhRefreshToken) -> None:
        await self._db(lambda s: revoke_connection(s, token.client_id, token.user_id))


def _client_id(client: OAuthClientInformationFull) -> str:
    if client.client_id is None:
        raise ValueError("a registered client has an id")
    return client.client_id
