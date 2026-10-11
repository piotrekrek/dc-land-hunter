from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.http import PoliteClient, RobotsDisallowedError
from tests.conftest import FakeClock

ClientFactory = Callable[..., PoliteClient]


def ok_handler(robots: str = "", robots_status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, text=robots, headers={"content-type": "text/plain"})
        return httpx.Response(200, text="ok")

    return handler


def test_user_agent_carries_contact(make_client: ClientFactory) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(404 if request.url.path == "/robots.txt" else 200)

    make_client(handler).get("https://a.example.pl/data")
    assert seen and all("mailto:test@example.org" in ua for ua in seen)


def test_rate_limit_per_host(make_client: ClientFactory, fake_clock: FakeClock) -> None:
    client = make_client(ok_handler(), rate_limit_per_s=1.0)
    client.get("https://a.example.pl/1")  # robots.txt + request: one sleep between them
    client.get("https://a.example.pl/2")
    client.get("https://b.example.pl/1")  # new host: robots.txt goes at once, then one wait
    waits = [s for s in fake_clock.sleeps if s > 0]
    assert waits == [pytest.approx(1.0), pytest.approx(1.0), pytest.approx(1.0)]


def test_robots_disallow_blocks(make_client: ClientFactory) -> None:
    client = make_client(ok_handler("User-agent: *\nDisallow: /private\n"))
    assert client.get("https://a.example.pl/public").status_code == 200
    with pytest.raises(RobotsDisallowedError):
        client.get("https://a.example.pl/private/x")


def test_missing_robots_allows(make_client: ClientFactory) -> None:
    client = make_client(ok_handler(robots_status=404))
    assert client.allowed("https://a.example.pl/anything")


def test_robots_server_error_disallows(make_client: ClientFactory) -> None:
    client = make_client(ok_handler(robots_status=500), retries=0)
    assert not client.allowed("https://a.example.pl/anything")


def test_retries_transient_errors(make_client: ClientFactory, fake_clock: FakeClock) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] < 3 else 200)

    resp = make_client(handler, retries=3, backoff_s=2.0).get("https://a.example.pl/x")
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert 2.0 in fake_clock.sleeps and 4.0 in fake_clock.sleeps


def test_gives_up_after_retries(make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path == "/robots.txt" else 502)

    assert make_client(handler, retries=1).get("https://a.example.pl/x").status_code == 502


def test_download_streams_to_a_file_and_retries_transient_errors(
    make_client: ClientFactory, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, content=b"abc" * 1000)

    dest = tmp_path / "big.bin"
    headers = make_client(handler).download("https://example.org/big.bin", dest)
    assert dest.read_bytes() == b"abc" * 1000
    assert headers["content-length"] == "3000"
    assert calls["n"] == 2


def test_download_respects_robots_and_raises_on_client_errors(
    make_client: ClientFactory, tmp_path: Path
) -> None:
    def blocked(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, text="User-agent: *\nDisallow: /")
            if request.url.path == "/robots.txt"
            else httpx.Response(200)
        )

    with pytest.raises(RobotsDisallowedError):
        make_client(blocked).download("https://example.org/x", tmp_path / "x")

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        make_client(missing).download("https://example.org/x", tmp_path / "x")


def test_download_honours_retry_after_and_asks_for_an_uncompressed_body(
    make_client: ClientFactory, fake_clock: FakeClock, tmp_path: Path
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.headers["accept-encoding"])
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "60"})
        return httpx.Response(200, content=b"ok")

    make_client(handler).download("https://example.org/f", tmp_path / "f")
    assert seen == ["identity", "identity"]
    assert 60.0 in fake_clock.sleeps  # waited what the server asked, not just the 2 s backoff
