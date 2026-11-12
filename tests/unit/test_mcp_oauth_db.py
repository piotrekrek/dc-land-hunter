"""The Claude connector's OAuth provider against a real database. Every step builds a fresh provider: nothing may
live in the memory of one, so an API restart between two steps must change nothing."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AuthorizationParams, RegistrationError, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from dclh_api.mcp import oauth
from dclh_api.mcp.oauth import DclhOAuthProvider
from dclh_pipeline.db import get_engine, session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.oauth_admin import purge_oauth

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db"), pytest.mark.anyio]

PUBLIC_URL = "https://dclh.example"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            session.execute(text("DELETE FROM oauth_clients"))  # requests, codes and tokens cascade
            session.execute(text("DELETE FROM users WHERE email LIKE '%@oauth.test'"))

    wipe()
    yield
    wipe()


def provider() -> DclhOAuthProvider:
    return DclhOAuthProvider(PUBLIC_URL, sessionmaker(bind=get_engine(), expire_on_commit=False))


def client_info(client_id: str = "c1", redirect: str = CALLBACK) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=[AnyUrl(redirect)],
        token_endpoint_auth_method="none",
        client_name="Claude",
        scope="dclh.read dclh.write",
    )


def params(scopes: list[str] | None = None, state: str | None = "st4te") -> AuthorizationParams:
    return AuthorizationParams(
        state=state,
        scopes=scopes,
        code_challenge=CHALLENGE,
        redirect_uri=AnyUrl(CALLBACK),
        redirect_uri_provided_explicitly=True,
    )


def make_user(name: str = "ann", access: bool = True) -> int:
    with session_scope() as session:
        return int(
            session.execute(
                text(
                    "INSERT INTO users (email, password_hash, role, mcp_access) "
                    "VALUES (:e, 'x', 'member', :a) RETURNING id"
                ),
                {"e": f"{name}@oauth.test", "a": access},
            ).scalar_one()
        )


def set_access(user_id: int, access: bool) -> None:
    with session_scope() as session:
        session.execute(text("UPDATE users SET mcp_access = :a WHERE id = :u"), {"a": access, "u": user_id})


def approve(req_id: str, user_id: int) -> str:
    with session_scope() as session:
        user = session.get(UserRow, user_id)
        assert user is not None
        return oauth.approve_request(session, req_id, user)


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def count(table: str) -> int:
    with session_scope() as session:
        return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


async def start_request(scopes: list[str] | None = None, state: str | None = "st4te") -> str:
    """Registers the client and runs /authorize; returns the request id from the consent page URL."""
    client = await provider().get_client("c1")
    if client is None:  # the SDK registers each client once, under a fresh id
        await provider().register_client(client_info())
        client = await provider().get_client("c1")
    assert client is not None
    url = await provider().authorize(client, params(scopes, state))
    assert url.startswith(f"{PUBLIC_URL}/polacz-claude?req=")
    return query(url)["req"]


async def connect(user_id: int) -> tuple[OAuthClientInformationFull, str, str]:
    """The whole dance up to the tokens: returns the client, the access token and the refresh token."""
    req = await start_request()
    code = query(approve(req, user_id))["code"]
    client = await provider().get_client("c1")
    assert client is not None
    loaded = await provider().load_authorization_code(client, code)
    assert loaded is not None
    tokens = await provider().exchange_authorization_code(client, loaded)
    assert tokens.refresh_token is not None
    return client, tokens.access_token, tokens.refresh_token


async def test_full_flow_including_rotation_and_revocation() -> None:
    user_id = make_user()
    client, access, refresh = await connect(user_id)
    token = await provider().load_access_token(access)
    assert token is not None and token.user_id == user_id and token.client_id == "c1"
    assert token.scopes == ["dclh.read", "dclh.write"]

    old = await provider().load_refresh_token(client, refresh)
    assert old is not None
    rotated = await provider().exchange_refresh_token(client, old, old.scopes)
    assert rotated.refresh_token is not None and rotated.refresh_token != refresh
    assert await provider().load_refresh_token(client, refresh) is None  # the used one is gone
    with pytest.raises(TokenError):
        await provider().exchange_refresh_token(client, old, old.scopes)  # a replay fails

    new_token = await provider().load_access_token(rotated.access_token)
    assert new_token is not None
    await provider().revoke_token(new_token)
    assert await provider().load_access_token(rotated.access_token) is None
    assert await provider().load_refresh_token(client, rotated.refresh_token) is None


async def test_nothing_secret_is_stored_in_the_clear() -> None:
    _, access, refresh = await connect(make_user())
    with session_scope() as session:
        stored = session.execute(text("SELECT token_hash FROM oauth_tokens")).scalars().all()
    assert len(stored) == 2 and access not in stored and refresh not in stored
    assert all(len(h) == 64 for h in stored)


async def test_register_rejects_a_foreign_redirect_and_stores_nothing() -> None:
    with pytest.raises(RegistrationError) as err:
        await provider().register_client(client_info(redirect="https://evil.example/cb"))
    assert err.value.error == "invalid_redirect_uri"
    assert count("oauth_clients") == 0


async def test_register_stops_at_the_client_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 2)
    await provider().register_client(client_info("a"))
    await provider().register_client(client_info("b"))
    with pytest.raises(RegistrationError) as err:
        await provider().register_client(client_info("c"))
    assert err.value.error == "invalid_client_metadata"


async def test_a_code_is_single_use() -> None:
    user_id = make_user()
    req = await start_request()
    code = query(approve(req, user_id))["code"]
    client = await provider().get_client("c1")
    assert client is not None
    loaded = await provider().load_authorization_code(client, code)
    assert loaded is not None
    await provider().exchange_authorization_code(client, loaded)
    with pytest.raises(TokenError):
        await provider().exchange_authorization_code(client, loaded)
    assert await provider().load_authorization_code(client, code) is None


async def test_an_expired_code_is_not_loaded() -> None:
    req = await start_request()
    code = query(approve(req, make_user()))["code"]
    with session_scope() as session:
        session.execute(text("UPDATE oauth_codes SET expires_at = now() - interval '1 second'"))
    client = await provider().get_client("c1")
    assert client is not None
    assert await provider().load_authorization_code(client, code) is None


async def test_a_code_of_another_client_is_not_loaded() -> None:
    req = await start_request()
    code = query(approve(req, make_user()))["code"]
    await provider().register_client(client_info("c2"))
    other = await provider().get_client("c2")
    assert other is not None
    assert await provider().load_authorization_code(other, code) is None


async def test_switching_access_off_kills_the_token_at_once() -> None:
    user_id = make_user()
    _, access, _ = await connect(user_id)
    assert await provider().load_access_token(access) is not None
    set_access(user_id, False)
    assert await provider().load_access_token(access) is None
    set_access(user_id, True)
    assert await provider().load_access_token(access) is not None


async def test_switching_access_off_also_blocks_a_refresh() -> None:
    user_id = make_user()
    client, _, refresh = await connect(user_id)
    old = await provider().load_refresh_token(client, refresh)
    assert old is not None
    set_access(user_id, False)
    with pytest.raises(TokenError):
        await provider().exchange_refresh_token(client, old, old.scopes)


async def test_an_expired_access_token_is_not_loaded() -> None:
    _, access, _ = await connect(make_user())
    with session_scope() as session:
        session.execute(
            text("UPDATE oauth_tokens SET expires_at = now() - interval '1 second' WHERE kind = 'access'")
        )
    assert await provider().load_access_token(access) is None


async def test_a_user_without_access_cannot_approve_and_the_request_stays() -> None:
    user_id = make_user(access=False)
    req = await start_request()
    with pytest.raises(PermissionError):
        approve(req, user_id)
    assert count("oauth_codes") == 0
    set_access(user_id, True)  # the admin switches it on; the same link works
    assert "code" in query(approve(req, user_id))


async def test_a_request_is_used_once_and_expires() -> None:
    user_id = make_user()
    req = await start_request()
    approve(req, user_id)
    with pytest.raises(LookupError):
        approve(req, user_id)
    assert count("oauth_codes") == 1  # no second code

    req2 = await start_request()
    with session_scope() as session:
        session.execute(text("UPDATE oauth_requests SET expires_at = now() - interval '1 second'"))
        assert oauth.pending_request(session, req2) is None
    with pytest.raises(LookupError):
        approve(req2, user_id)


async def test_an_unknown_request_is_refused() -> None:
    with session_scope() as session, pytest.raises(LookupError):
        oauth.deny_request(session, "never-issued")


async def test_deny_sends_access_denied_with_the_state_and_drops_the_request() -> None:
    req = await start_request(state="keep-me")
    with session_scope() as session:
        redirect = oauth.deny_request(session, req)
    assert redirect.startswith(CALLBACK)
    assert query(redirect) == {"error": "access_denied", "state": "keep-me"}
    assert count("oauth_requests") == 0


async def test_the_approved_redirect_carries_code_and_state() -> None:
    req = await start_request(state="keep-me")
    redirect = approve(req, make_user())
    assert redirect.startswith(CALLBACK)
    assert set(query(redirect)) == {"code", "state"} and query(redirect)["state"] == "keep-me"


async def test_the_consent_page_can_describe_the_pending_request() -> None:
    req = await start_request(scopes=["dclh.read"])
    with session_scope() as session:
        pending = oauth.pending_request(session, req)
    assert pending is not None and pending.client_name == "Claude" and pending.scopes == ["dclh.read"]


async def test_without_requested_scopes_the_clients_registered_scopes_apply() -> None:
    req = await start_request(scopes=None)
    with session_scope() as session:
        pending = oauth.pending_request(session, req)
    assert pending is not None and pending.scopes == ["dclh.read", "dclh.write"]


async def test_purge_removes_what_expired_and_idle_clients() -> None:
    user_id = make_user()
    await connect(user_id)  # client c1 with a live token
    await provider().register_client(client_info("old-idle"))
    await provider().register_client(client_info("fresh-idle"))
    with session_scope() as session:
        session.execute(
            text(
                "UPDATE oauth_clients SET created_at = now() - interval '8 days' WHERE client_id <> 'fresh-idle'"
            )
        )
        session.execute(
            text(
                "INSERT INTO oauth_requests (id_hash, client_id, params, expires_at) "
                "VALUES ('r', 'c1', '{}', now() - interval '1 minute')"
            )
        )
        session.execute(
            text(
                "INSERT INTO oauth_codes (code_hash, client_id, user_id, params, expires_at) "
                "VALUES ('k', 'c1', :u, '{}', now() - interval '1 minute')"
            ),
            {"u": user_id},
        )
        session.execute(
            text(
                "INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, scopes, expires_at) "
                "VALUES ('t', 'access', 'c1', :u, '{}', now() - interval '1 minute')"
            ),
            {"u": user_id},
        )
    with session_scope() as session:
        removed = purge_oauth(session, datetime.now(UTC))
    assert removed == 4  # the expired request, code and token, and the old idle client
    with session_scope() as session:
        kept = set(session.execute(text("SELECT client_id FROM oauth_clients")).scalars())
    assert kept == {"c1", "fresh-idle"}
    assert count("oauth_tokens") == 2  # the live pair is untouched
    # A month on, the live pair has expired too, and the client that held it is idle.
    with session_scope() as session:
        purge_oauth(session, datetime.now(UTC) + timedelta(days=31))
    assert count("oauth_tokens") == 0 and count("oauth_clients") == 0
