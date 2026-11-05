"""The Claude connector as the API serves it: mounted only when DCLH_PUBLIC_URL is set, discoverable, and locked."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dclh_api.main import create_app
from dclh_pipeline.settings import Settings, get_settings

PUBLIC_URL = "https://dclh.example"
SCOPES = ["dclh.read", "dclh.write"]


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DCLH_PUBLIC_URL", PUBLIC_URL)
    get_settings.cache_clear()
    with TestClient(create_app(), base_url=PUBLIC_URL) as client:  # the context runs the app's lifespan
        yield client
    get_settings.cache_clear()


def test_the_resource_metadata_names_the_server_and_its_authorization_server(connector: TestClient) -> None:
    body = connector.get("/.well-known/oauth-protected-resource/mcp").json()
    assert body["resource"] == f"{PUBLIC_URL}/mcp"
    assert body["authorization_servers"] == [f"{PUBLIC_URL}/"]
    assert body["scopes_supported"] == SCOPES  # both, so a client asks for both


def test_the_authorization_server_metadata_points_at_our_endpoints(connector: TestClient) -> None:
    body = connector.get("/.well-known/oauth-authorization-server").json()
    assert body["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
    assert body["token_endpoint"] == f"{PUBLIC_URL}/token"
    assert body["registration_endpoint"] == f"{PUBLIC_URL}/register"
    assert body["revocation_endpoint"] == f"{PUBLIC_URL}/revoke"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["scopes_supported"] == SCOPES
    assert body["response_types_supported"] == ["code"]


def test_mcp_without_a_token_is_401_and_says_where_the_metadata_is(connector: TestClient) -> None:
    response = connector.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert f'resource_metadata="{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp"' in challenge


def test_mcp_with_a_made_up_token_is_401(connector: TestClient) -> None:
    # The lookup needs the database; with none reachable the token is simply not valid.
    response = connector.post("/mcp", headers={"Authorization": "Bearer not-a-token"}, json={})
    assert response.status_code == 401


def test_the_api_routes_are_unchanged(connector: TestClient) -> None:
    assert connector.get("/api/health").json()["version"]
    assert connector.get("/api/oauth/connections").status_code == 401  # still the API's own login
    assert connector.get("/api/no-such-route").status_code == 404


def test_without_a_public_url_nothing_of_the_connector_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DCLH_PUBLIC_URL", raising=False)
    monkeypatch.setattr(
        "dclh_pipeline.settings.Settings.model_config", {**Settings.model_config, "env_file": None}
    )
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            for path in ("/mcp", "/authorize", "/token", "/.well-known/oauth-authorization-server"):
                assert client.get(path).status_code == 404, path
            assert client.get("/api/health").status_code == 200
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("given", "stored"), [("https://dclh.example/", PUBLIC_URL), ("  ", None), ("", None)]
)
def test_the_public_url_is_cleaned(given: str, stored: str | None) -> None:
    assert Settings(public_url=given).public_url == stored


def test_a_public_url_without_a_scheme_is_refused() -> None:
    with pytest.raises(ValidationError, match="DCLH_PUBLIC_URL must start with https://"):
        Settings(public_url="dclh.example")
