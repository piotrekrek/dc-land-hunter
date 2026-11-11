"""`/api/team`: an admin switches Claude access for the team from the app (PLAN.md, "Claude tab")."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dclh_api.main import create_app
from dclh_api.security import current_user
from dclh_pipeline.db import session_scope
from dclh_pipeline.db.models import UserRow
from tests.unit.test_mcp_oauth_db import make_user
from tests.unit.test_oauth_router import connect, usable

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


def as_user(user_id: int, role: str) -> Iterator[TestClient]:
    with session_scope() as session:
        session.execute(text("UPDATE users SET role = :r WHERE id = :u"), {"r": role, "u": user_id})
        user = session.get(UserRow, user_id)
        assert user is not None
    app = create_app()
    app.dependency_overrides[current_user] = lambda: user
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin() -> Iterator[TestClient]:
    yield from as_user(make_user("boss"), "admin")


@pytest.fixture
def member() -> Iterator[TestClient]:
    yield from as_user(make_user("ann"), "member")


def ours(body: list[dict[str, object]]) -> list[dict[str, object]]:
    return [m for m in body if str(m["email"]).endswith("@oauth.test")]


def test_an_admin_sees_each_user_with_access_and_connected_apps(admin: TestClient) -> None:
    connect(make_user("ann"), "ann-web")
    make_user("bob", access=False)
    response = admin.get("/api/team")
    assert response.status_code == 200
    assert ours(response.json()) == [
        {"email": "ann@oauth.test", "role": "member", "mcp_access": True, "connections": 1},
        {"email": "bob@oauth.test", "role": "member", "mcp_access": False, "connections": 0},
        {"email": "boss@oauth.test", "role": "admin", "mcp_access": True, "connections": 0},
    ]


def test_switching_off_signs_claude_out_and_on_lets_the_user_connect_again(admin: TestClient) -> None:
    token = connect(make_user("ann"), "ann-web")
    off = admin.put("/api/team/Ann@OAuth.test/mcp-access", json={"on": False})
    assert off.status_code == 200
    assert off.json() == {"email": "ann@oauth.test", "role": "member", "mcp_access": False, "connections": 0}
    assert not usable(token)
    on = admin.put("/api/team/ann@oauth.test/mcp-access", json={"on": True})
    assert on.status_code == 200 and on.json()["mcp_access"] is True


def test_an_unknown_email_is_a_polish_404(admin: TestClient) -> None:
    response = admin.put("/api/team/nobody@oauth.test/mcp-access", json={"on": True})
    assert response.status_code == 404 and response.json()["detail"] == "Nie ma takiego użytkownika."


def test_a_member_can_neither_list_nor_switch(member: TestClient) -> None:
    make_user("bob", access=False)
    assert member.get("/api/team").status_code == 403
    assert member.put("/api/team/bob@oauth.test/mcp-access", json={"on": True}).status_code == 403
    with session_scope() as session:
        assert (
            session.execute(text("SELECT mcp_access FROM users WHERE email = 'bob@oauth.test'")).scalar_one()
            is False
        )


def test_without_a_session_the_api_answers_401() -> None:
    with TestClient(create_app()) as c:
        assert c.get("/api/team").status_code == 401
