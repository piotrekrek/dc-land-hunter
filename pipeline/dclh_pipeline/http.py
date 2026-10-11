"""Polite HTTP client shared by every connector.

Rules from the brief (section 4): identify with a User-Agent and contact email, keep to <= 1 request/s
per host by default, honour robots.txt and retry transient failures with backoff.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class RobotsDisallowedError(RuntimeError):
    """robots.txt forbids this URL for our User-Agent."""


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str,
        rate_limit_per_s: float = 1.0,
        timeout_s: float = 30.0,
        retries: int = 3,
        backoff_s: float = 2.0,
        respect_robots: bool = True,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval_s = 1.0 / rate_limit_per_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.respect_robots = respect_robots
        self._clock = clock
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "pl,en;q=0.5"},
            # Fail fast on unreachable hosts (connect), but let slow WFS responses finish (read).
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            follow_redirects=True,
            transport=transport,
        )

    # -- context manager ------------------------------------------------------------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- public API -----------------------------------------------------------------------

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("HEAD", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self.respect_robots and not self.allowed(url):
            raise RobotsDisallowedError(url)
        return self._send_with_retries(method, url, **kwargs)

    def download(self, url: str, dest: Path) -> httpx.Headers:
        """Stream a large file to `dest` without holding it in memory; returns the response headers.

        Honours robots.txt, the per-host rate limit and Retry-After, and retries transient failures (a failed
        attempt starts the file again). `dest` is overwritten. The server is asked not to compress the body,
        so the bytes written are the bytes announced in Content-Length.
        """
        if self.respect_robots and not self.allowed(url):
            raise RobotsDisallowedError(url)
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._throttle(host)
            try:
                with self._client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
                    if resp.status_code not in RETRY_STATUSES or attempt >= self.retries:
                        resp.raise_for_status()
                        with dest.open("wb") as fh:
                            for chunk in resp.iter_bytes(1 << 20):
                                fh.write(chunk)
                        return resp.headers
                    retry_after = resp.headers.get("retry-after", "")
                    if retry_after.isdigit():
                        self._sleep(min(float(retry_after), 120.0))
            except httpx.TransportError:
                if attempt >= self.retries:
                    raise
            self._sleep(self.backoff_s * (2**attempt))
            attempt += 1

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = self._load_robots(origin)
            self._robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    # -- internals ------------------------------------------------------------------------

    def _load_robots(self, origin: str) -> RobotFileParser:
        parser = RobotFileParser()
        try:
            # One attempt only: if the host is down, the real request reports it.
            resp = self._send_with_retries("GET", f"{origin}/robots.txt", retries=0)
        except httpx.TransportError:
            # Unreachable host: the real request will fail with a clearer error, so don't block here.
            parser.parse([])
            return parser
        if resp.status_code >= 500:
            # RFC 9309 section 2.3.1.4: server errors mean "assume complete disallow".
            parser.parse(["User-agent: *", "Disallow: /"])
        elif resp.status_code >= 400 or "html" in resp.headers.get("content-type", ""):
            # Missing robots.txt (or an HTML error page served with 200) means no restrictions.
            parser.parse([])
        else:
            parser.parse(resp.text.splitlines())
        return parser

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_interval_s - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _send_with_retries(
        self, method: str, url: str, *, retries: int | None = None, **kwargs: Any
    ) -> httpx.Response:
        host = urlsplit(url).netloc
        max_retries = self.retries if retries is None else retries
        attempt = 0
        while True:
            self._throttle(host)
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError:
                if attempt >= max_retries:
                    raise
            else:
                if resp.status_code not in RETRY_STATUSES or attempt >= max_retries:
                    return resp
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    self._sleep(min(float(retry_after), 120.0))
            self._sleep(self.backoff_s * (2**attempt))
            attempt += 1
