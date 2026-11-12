"""The consent step and the connection list (`/api/oauth/...`) against a real database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dclh_api.main import create_app
from dclh_api.mcp import oauth
from dclh_api.routers.oauth import NO_ACCESS, REQUEST_GONE
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from dclh_pipeline.settings import Settings
from tests.unit.test_mcp_oauth_db import CALLBACK, PUBLIC_URL, client_info, make_user, params, query

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("migrated_db")]


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def wipe() -> None:
        with session_scope() as session:
            session.execute(text("DELETE FROM oauth_clients"))
            session.execute(text("DELETE FROM users WHERE email LIKE '%@oauth.test'"))

    wipe()
    yield
    wipe()


def client_for(user_id: int) -> Iterator[TestClient]:
    with session_scope() as session:
        user = session.get(UserRow, user_id)
        assert user is not None
    app = create_app()
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as c:
        yield c


def new_request(client_id: str = "c1", scopes: list[str] | None = None) -> str:
    """A client that went through /authorize: returns the request id the consent page is opened with."""
    with session_scope() as session:
        info = client_info(client_id)
        oauth.register_client(session, info)
        return query(oauth.create_request(session, PUBLIC_URL, info, params(scopes)))["req"]


def connect(user_id: int, client_id: str) -> str:
    """Runs the whole flow for `user_id` and returns the access token."""
    req = new_request(client_id)
    with session_scope() as session:
        user = session.get(UserRow, user_id)
        assert user is not None
        code = query(oauth.approve_request(session, req, user))["code"]
        loaded = oauth.load_code(session, client_id, code)
        assert loaded is not None
        return oauth.exchange_code(session, client_id, loaded).access_token


def usable(token: str) -> bool:
    with session_scope() as session:
        return oauth.load_access_token(session, token) is not None


@pytest.fixture
def ann() -> Iterator[TestClient]:
    yield from client_for(make_user("ann"))


@pytest.fixture
def nobody() -> Iterator[TestClient]:
    """A user whose admin has not switched Claude access on."""
    yield from client_for(make_user("nobody", access=False))


def test_the_consent_info_names_the_client_the_scopes_and_the_account(ann: TestClient) -> None:
    body = ann.get(f"/api/oauth/requests/{new_request()}").json()
    assert body["client_name"] == "Claude" and body["user_email"] == "ann@oauth.test"
    assert body["can_connect"] is True
    assert [s["id"] for s in body["scopes"]] == ["dclh.read", "dclh.write"]
    assert body["scopes"][0]["label"].startswith("czytać lokalizacje")
    assert "bez notatek" in body["scopes"][0]["label"]


def test_an_unknown_or_expired_request_is_a_polish_404(ann: TestClient) -> None:
    for call in (
        lambda r: ann.get(f"/api/oauth/requests/{r}"),
        lambda r: ann.post(f"/api/oauth/requests/{r}/decision", json={"approve": True}),
        lambda r: ann.post(f"/api/oauth/requests/{r}/decision", json={"approve": False}),
    ):
        response = call("never-issued")
        assert response.status_code == 404 and response.json()["detail"] == REQUEST_GONE
    req = new_request()
    with session_scope() as session:
        session.execute(text("UPDATE oauth_requests SET expires_at = now() - interval '1 second'"))
    assert ann.get(f"/api/oauth/requests/{req}").status_code == 404


def test_a_user_without_access_is_told_so_and_cannot_approve(nobody: TestClient) -> None:
    req = new_request()
    assert nobody.get(f"/api/oauth/requests/{req}").json()["can_connect"] is False
    response = nobody.post(f"/api/oauth/requests/{req}/decision", json={"approve": True})
    assert response.status_code == 403 and response.json()["detail"] == NO_ACCESS
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM oauth_codes")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM oauth_requests")).scalar_one() == 1  # still there


def test_approving_redirects_to_the_client_with_a_code_and_the_state(ann: TestClient) -> None:
    redirect = ann.post(f"/api/oauth/requests/{new_request()}/decision", json={"approve": True}).json()[
        "redirect_to"
    ]
    assert redirect.startswith(CALLBACK)
    assert set(query(redirect)) == {"code", "state"} and query(redirect)["state"] == "st4te"


def test_refusing_redirects_with_access_denied_even_without_access(nobody: TestClient) -> None:
    redirect = nobody.post(f"/api/oauth/requests/{new_request()}/decision", json={"approve": False}).json()[
        "redirect_to"
    ]
    assert redirect.startswith(CALLBACK) and query(redirect)["error"] == "access_denied"


def test_a_request_can_be_decided_only_once(ann: TestClient) -> None:
    req = new_request()
    assert ann.post(f"/api/oauth/requests/{req}/decision", json={"approve": True}).status_code == 200
    again = ann.post(f"/api/oauth/requests/{req}/decision", json={"approve": True})
    assert again.status_code == 404 and again.json()["detail"] == REQUEST_GONE
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM oauth_codes")).scalar_one() == 1


def test_connections_list_only_the_current_users_clients() -> None:
    ann_id, bob_id = make_user("ann"), make_user("bob")
    connect(ann_id, "ann-client")
    connect(bob_id, "bob-client")
    ann = next(client_for(ann_id))
    items = ann.get("/api/oauth/connections").json()["items"]
    assert [i["client_id"] for i in items] == ["ann-client"]
    assert items[0]["client_name"] == "Claude"
    assert datetime.fromisoformat(items[0]["connected_at"]) <= datetime.now(UTC)


def test_disconnecting_kills_that_users_token_and_leaves_other_users_alone() -> None:
    ann_id, bob_id = make_user("ann"), make_user("bob")
    ann_token, bob_token = connect(ann_id, "ann-client"), connect(bob_id, "bob-client")
    ann = next(client_for(ann_id))
    # Another user's client id is not Ann's to drop: nothing happens, and it is not an error.
    assert ann.delete("/api/oauth/connections/bob-client").status_code == 204
    assert usable(bob_token) and usable(ann_token)
    assert ann.delete("/api/oauth/connections/ann-client").status_code == 204
    assert not usable(ann_token) and usable(bob_token)
    assert ann.get("/api/oauth/connections").json()["items"] == []
    assert ann.delete("/api/oauth/connections/ann-client").status_code == 204  # idempotent


def test_login_and_me_report_the_mcp_access_flag(ann: TestClient, nobody: TestClient) -> None:
    assert ann.get("/api/auth/me").json()["mcp_access"] is True
    assert nobody.get("/api/auth/me").json()["mcp_access"] is False


def test_the_oauth_routes_require_login() -> None:
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/oauth/requests/x").status_code == 401
        assert anonymous.post("/api/oauth/requests/x/decision", json={"approve": True}).status_code == 401
        assert anonymous.get("/api/oauth/connections").status_code == 401
        assert anonymous.delete("/api/oauth/connections/x").status_code == 401


def test_connections_carry_the_connector_address_or_null_when_it_is_off(
    ann: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "dclh_api.routers.oauth.get_settings", lambda: Settings(public_url="https://dclh.example/")
    )
    assert ann.get("/api/oauth/connections").json()["connector_url"] == "https://dclh.example/mcp"
    monkeypatch.setattr("dclh_api.routers.oauth.get_settings", lambda: Settings(public_url=None))
    assert ann.get("/api/oauth/connections").json()["connector_url"] is None
