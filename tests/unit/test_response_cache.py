from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.response_cache import ResponseCache

ClockFn = Callable[[], float]


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make_client(
    tmp_path: Path, clock: Clock, handler: Callable[[httpx.Request], httpx.Response]
) -> PoliteClient:
    cache = ResponseCache(tmp_path / "cache", timedelta(days=40), clock=clock)
    return PoliteClient(
        user_agent="test (mailto:test@example.org)",
        rate_limit_per_s=1000.0,
        respect_robots=False,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        cache=cache,
    )


def counting_handler(
    body: bytes = b"<gml/>", status: int = 200
) -> tuple[list[httpx.Request], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=body, headers={"content-type": "application/xml"})

    return seen, handler


def test_second_identical_request_is_served_from_cache(tmp_path: Path, clock: Clock) -> None:
    seen, handler = counting_handler()
    client = make_client(tmp_path, clock, handler)
    first = client.get("https://a.example.pl/wfs", params={"startIndex": 0, "count": 1000})
    second = client.get("https://a.example.pl/wfs", params={"count": 1000, "startIndex": 0})
    assert len(seen) == 1
    assert second.content == first.content == b"<gml/>"
    assert second.headers["content-type"] == "application/xml"
    second.raise_for_status()  # a cached answer behaves like a live one


def test_different_parameters_are_different_answers(tmp_path: Path, clock: Clock) -> None:
    seen, handler = counting_handler()
    client = make_client(tmp_path, clock, handler)
    client.get("https://a.example.pl/wfs", params={"startIndex": 0})
    client.get("https://a.example.pl/wfs", params={"startIndex": 1000})
    assert len(seen) == 2


def test_answer_older_than_the_ttl_is_asked_again(tmp_path: Path, clock: Clock) -> None:
    seen, handler = counting_handler()
    client = make_client(tmp_path, clock, handler)
    client.get("https://a.example.pl/wfs", params={"q": 1})
    clock.now += timedelta(days=39).total_seconds()
    client.get("https://a.example.pl/wfs", params={"q": 1})
    assert len(seen) == 1
    clock.now += timedelta(days=2).total_seconds()
    client.get("https://a.example.pl/wfs", params={"q": 1})
    assert len(seen) == 2


def test_wfs_exception_report_is_never_cached(tmp_path: Path, clock: Clock) -> None:
    body = b"<ows:ExceptionReport><ows:ExceptionText>blad</ows:ExceptionText></ows:ExceptionReport>"
    seen, handler = counting_handler(body=body)
    client = make_client(tmp_path, clock, handler)
    client.get("https://a.example.pl/wfs", params={"q": 1})
    client.get("https://a.example.pl/wfs", params={"q": 1})
    assert len(seen) == 2


@pytest.mark.parametrize("status", [404, 500])
def test_error_answers_are_never_cached(tmp_path: Path, clock: Clock, status: int) -> None:
    seen, handler = counting_handler(status=status)
    client = make_client(tmp_path, clock, handler)
    client.get("https://a.example.pl/wfs", params={"q": 1})
    client.get("https://a.example.pl/wfs", params={"q": 1})
    assert len(seen) >= 2


def test_without_a_cache_every_request_goes_out(tmp_path: Path, clock: Clock) -> None:
    seen, handler = counting_handler()
    client = PoliteClient(
        user_agent="test (mailto:test@example.org)",
        rate_limit_per_s=1000.0,
        respect_robots=False,
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    client.get("https://a.example.pl/wfs", params={"q": 1})
    client.get("https://a.example.pl/wfs", params={"q": 1})
    assert len(seen) == 2


def fill(cache: ResponseCache, clock: Clock, n: int, batch: str = "a") -> list[str]:
    """`n` answers of about 1 kB, one minute apart; each batch asks for different URLs."""
    keys = []
    for i in range(n):
        key = ResponseCache.key("GET", "https://a.example.pl/wfs", {"batch": batch, "i": i})
        cache.put(key, httpx.Response(200, content=b"x" * 1000, headers={"content-type": "application/xml"}))
        keys.append(key)
        clock.now += 60.0
    return keys


def test_prune_deletes_expired_answers_and_keeps_fresh_ones(tmp_path: Path, clock: Clock) -> None:
    cache = ResponseCache(tmp_path / "cache", timedelta(days=40), clock=clock)
    old = fill(cache, clock, 2)
    clock.now += timedelta(days=41).total_seconds()
    fresh = fill(cache, clock, 3, batch="b")
    removed, freed = cache.prune(max_bytes=10**9)
    assert removed == 2 and freed > 2000
    assert all(cache.get(k, "https://a.example.pl/wfs") is None for k in old)
    assert all(cache.get(k, "https://a.example.pl/wfs") is not None for k in fresh)


def test_prune_deletes_the_oldest_answers_until_the_cache_fits(tmp_path: Path, clock: Clock) -> None:
    cache = ResponseCache(tmp_path / "cache", timedelta(days=40), clock=clock)
    keys = fill(cache, clock, 5)
    removed, _ = cache.prune(max_bytes=2500)  # room for two answers of about 1 kB each
    assert removed == 3
    kept = [k for k in keys if cache.get(k, "https://a.example.pl/wfs") is not None]
    assert kept == keys[3:]  # the newest two stay
    assert cache.prune(max_bytes=2500) == (0, 0)  # already in bounds: nothing more goes


def test_prune_drops_an_answer_with_broken_metadata_and_copes_with_no_folder(
    tmp_path: Path, clock: Clock
) -> None:
    assert ResponseCache(tmp_path / "missing", timedelta(days=40), clock=clock).prune(max_bytes=0) == (0, 0)
    cache = ResponseCache(tmp_path / "cache", timedelta(days=40), clock=clock)
    (key,) = fill(cache, clock, 1)
    (tmp_path / "cache" / key[:2] / f"{key}.json").write_text("not json", encoding="utf-8")
    assert cache.prune(max_bytes=10**9)[0] == 1
    assert not list((tmp_path / "cache").rglob("*.*"))
