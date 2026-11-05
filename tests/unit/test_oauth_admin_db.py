"""`dclh user mcp-access` and `dclh oauth ...`: who may connect Claude, what is connected, the clean-up."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from dclh_pipeline import cli
from dclh_pipeline.db import session_scope
from dclh_pipeline.oauth_admin import list_connections, set_mcp_access
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


def access_of(email: str) -> bool:
    with session_scope() as session:
        return bool(
            session.execute(text("SELECT mcp_access FROM users WHERE email = :e"), {"e": email}).scalar_one()
        )


def test_switching_access_off_drops_the_tokens_and_the_user_has_to_connect_again() -> None:
    user_id = make_user("ann")
    token = connect(user_id, "ann-client")
    assert usable(token)
    with session_scope() as session:
        set_mcp_access(session, "Ann@OAuth.test", False)  # the email is matched case-insensitively
    assert not access_of("ann@oauth.test") and not usable(token)
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM oauth_tokens")).scalar_one() == 0
        set_mcp_access(session, "ann@oauth.test", True)
    assert access_of("ann@oauth.test") and not usable(token)  # on again, but the old token stays dead


def test_an_unknown_user_is_an_error() -> None:
    with session_scope() as session, pytest.raises(LookupError):
        set_mcp_access(session, "nobody@oauth.test", True)


def test_connections_are_listed_per_user_and_app() -> None:
    connect(make_user("ann"), "ann-client")
    connect(make_user("bob"), "bob-client")
    with session_scope() as session:
        rows = list_connections(session)
    assert [(c.email, c.client_id, c.client_name) for c in rows] == [
        ("ann@oauth.test", "ann-client", "Claude"),
        ("bob@oauth.test", "bob-client", "Claude"),
    ]
    assert all(c.expires_at > c.connected_at for c in rows)


def test_the_cli_switches_access_and_lists_and_purges() -> None:
    make_user("ann", access=False)
    runner = CliRunner()
    on = runner.invoke(cli.app, ["user", "mcp-access", "ann@oauth.test", "--on"])
    assert on.exit_code == 0, on.output
    assert "Claude access ON for ann@oauth.test" in on.output and access_of("ann@oauth.test")

    connect(make_user("bob"), "bob-client")
    listed = runner.invoke(cli.app, ["oauth", "list"])
    assert listed.exit_code == 0 and "bob@oauth.test" in listed.output and "Claude" in listed.output

    off = runner.invoke(cli.app, ["user", "mcp-access", "ann@oauth.test", "--off"])
    assert off.exit_code == 0 and "OFF" in off.output and not access_of("ann@oauth.test")
    purged = runner.invoke(cli.app, ["oauth", "purge"])
    assert purged.exit_code == 0 and purged.output.startswith("removed ")


def test_the_cli_refuses_an_unknown_user() -> None:
    result = CliRunner().invoke(cli.app, ["user", "mcp-access", "nobody@oauth.test", "--on"])
    assert result.exit_code == 1 and "no such user" in result.output
