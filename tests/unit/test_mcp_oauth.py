"""The redirect URI allowlist of the Claude connector: a code may only go to Claude or to a loopback address."""

from __future__ import annotations

import pytest

from dclh_api.mcp.oauth import redirect_uri_allowed


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        "http://localhost:33418/callback",
        "http://127.0.0.1:6274/oauth/callback",
        "http://[::1]:8080/cb",
    ],
)
def test_allowed(uri: str) -> None:
    assert redirect_uri_allowed(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai.evil.com/api/mcp/auth_callback",  # a lookalike host
        "https://evil.com/claude.ai/api/mcp/auth_callback",
        "https://claude.ai@evil.com/api/mcp/auth_callback",  # claude.ai is only the userinfo
        "https://claude.ai\\@evil.com/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",  # not https
        "https://claude.ai:8443/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/other",
        "https://claude.ai/api/mcp/auth_callback?next=https://evil.com",
        "https://claude.ai/api/mcp/auth_callback#frag",
        "https://example.com/cb",
        "https://localhost/cb",  # loopback is for plain http only
        "http://localhost.evil.com/cb",
        "http://evil.com/localhost",
        "http://user@localhost/cb",
        "javascript:alert(1)",
        "claude://callback",
        "",
        "not a uri",
    ],
)
def test_refused(uri: str) -> None:
    assert not redirect_uri_allowed(uri)
