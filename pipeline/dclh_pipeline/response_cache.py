"""Answer cache for GET requests, so a repeat search does not download the same tiles and points again.

Used by `dclh search` only: parcels, KIUG and KIMPZP samples and the other WFS pages it reads. A cached answer
is served for `ttl`, so a monthly refresh reuses the last month's downloads. A change upstream shows up on the
first refresh after the answer expires. Only plain 200 answers are kept: an error, an empty body or a WFS
ExceptionReport is always asked again.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

_EXCEPTION_MARK = b"ExceptionReport"


class ResponseCache:
    def __init__(self, root: Path, ttl: timedelta, clock: Callable[[], float] = time.time) -> None:
        self.root = root
        self.ttl_s = ttl.total_seconds()
        self._clock = clock

    @staticmethod
    def key(method: str, url: str, params: Any = None) -> str:
        """Stable key for a request: the same URL and parameters in any order give the same key."""
        payload = json.dumps([method, url, sorted((str(k), str(v)) for k, v in dict(params or {}).items())])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _paths(self, key: str) -> tuple[Path, Path]:
        folder = self.root / key[:2]
        return folder / f"{key}.body", folder / f"{key}.json"

    def get(self, key: str, url: str) -> httpx.Response | None:
        body_path, meta_path = self._paths(key)
        if not meta_path.exists() or not body_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if self._clock() - float(meta["fetched_at"]) > self.ttl_s:
            return None
        return httpx.Response(
            200,
            content=body_path.read_bytes(),
            headers={"content-type": meta["content_type"]},
            request=httpx.Request("GET", url),
        )

    def put(self, key: str, response: httpx.Response) -> None:
        """Keep a plain 200 answer with a body; anything else is left to be asked again."""
        body = response.content
        if response.status_code != 200 or not body or _EXCEPTION_MARK in body[:4096]:
            return
        body_path, meta_path = self._paths(key)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta = {
            "fetched_at": self._clock(),
            "content_type": response.headers.get("content-type", "application/octet-stream"),
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
