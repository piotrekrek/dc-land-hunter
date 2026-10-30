"""Answer cache for GET requests, so a repeat search does not download the same tiles and points again.

Used by `dclh search` only: parcels, KIUG and KIMPZP samples and the other WFS pages it reads. A cached answer
is served for `ttl`, so a monthly refresh reuses the last month's downloads. A change upstream shows up on the
first refresh after the answer expires. Only plain 200 answers are kept: an error, an empty body or a WFS
ExceptionReport is always asked again.

Nothing is deleted on read, so `prune` keeps the folder in bounds: each search calls it before it starts.
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
        self.hits = 0  # answers served from disk and requests that had to go out: a check's "warm" flag
        self.misses = 0

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
            self.misses += 1
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if self._clock() - float(meta["fetched_at"]) > self.ttl_s:
            self.misses += 1
            return None
        self.hits += 1
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

    def prune(self, max_bytes: int) -> tuple[int, int]:
        """Delete expired answers, then the oldest ones until the cache holds at most `max_bytes`.

        Returns (answers deleted, bytes freed). An answer without readable metadata is deleted as well: it is
        never served (`get` needs both files), so it only takes space.
        """
        entries: list[tuple[float, int, Path, Path]] = []
        for meta_path in self.root.glob("*/*.json"):
            body_path = meta_path.with_suffix(".body")
            try:
                fetched_at = float(json.loads(meta_path.read_text(encoding="utf-8"))["fetched_at"])
            except (OSError, ValueError, KeyError, TypeError):
                fetched_at = float("-inf")
            size = meta_path.stat().st_size + (body_path.stat().st_size if body_path.exists() else 0)
            entries.append((fetched_at, size, meta_path, body_path))
        entries.sort(key=lambda e: e[0])
        total = sum(e[1] for e in entries)
        oldest_kept = self._clock() - self.ttl_s
        removed = freed = 0
        for fetched_at, size, meta_path, body_path in entries:
            if fetched_at >= oldest_kept and total - freed <= max_bytes:
                break
            meta_path.unlink(missing_ok=True)
            body_path.unlink(missing_ok=True)
            removed += 1
            freed += size
        return removed, freed
