"""Content-addressed store for raw source snapshots.

Layout under `data/raw/<source_id>/`:
- `objects/<sha256[:2]>/<sha256><suffix>`: raw bytes, stored once per distinct content.
- `manifest.jsonl`: one line per fetch (date, hash, URL, whether parse succeeded).

A fetch that returns identical bytes adds a manifest line (so `as_of` moves forward) but no new object.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    source_id: str
    fetched_at: datetime
    sha256: str
    path: Path
    size_bytes: int
    url: str | None = None
    content_type: str | None = None
    new_content: bool = True  # False when identical to the previous snapshot of this source
    good: bool = False  # True once parse() succeeded on it

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["fetched_at"] = self.fetched_at.isoformat()
        data["path"] = str(self.path)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Snapshot:
        return cls(
            **{**data, "fetched_at": datetime.fromisoformat(data["fetched_at"]), "path": Path(data["path"])}
        )


def object_path(objects_dir: Path, sha: str, suffix: str) -> Path:
    """Where an object lives: `<objects_dir>/<sha[:2]>/<sha><suffix>`."""
    return objects_dir / sha[:2] / f"{sha}{suffix}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _source_dir(self, source_id: str) -> Path:
        return self.root / source_id

    def _manifest(self, source_id: str) -> Path:
        return self._source_dir(source_id) / "manifest.jsonl"

    def save(
        self,
        source_id: str,
        content: bytes,
        *,
        suffix: str = ".bin",
        url: str | None = None,
        content_type: str | None = None,
        fetched_at: datetime | None = None,
    ) -> Snapshot:
        tmp_dir = self._source_dir(source_id) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as fh:
            fh.write(content)
            tmp = Path(fh.name)
        return self.save_file(
            source_id, tmp, suffix=suffix, url=url, content_type=content_type, fetched_at=fetched_at
        )

    def save_file(
        self,
        source_id: str,
        file: Path,
        *,
        suffix: str = ".bin",
        url: str | None = None,
        content_type: str | None = None,
        fetched_at: datetime | None = None,
    ) -> Snapshot:
        """Move `file` into the store (large downloads are written to disk first, then saved)."""
        sha, target = self._put_object(source_id, file, suffix)
        previous = self.latest(source_id)
        snap = Snapshot(
            source_id=source_id,
            fetched_at=fetched_at or datetime.now(UTC),
            sha256=sha,
            path=target,
            size_bytes=target.stat().st_size,
            url=url,
            content_type=content_type,
            new_content=previous is None or previous.sha256 != sha,
        )
        with self._manifest(source_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap.to_json(), ensure_ascii=False) + "\n")
        return snap

    def put_object(self, source_id: str, content: bytes, *, suffix: str = ".bin") -> str:
        """Store raw bytes as an object without a manifest line; returns the sha256.

        For parts of a snapshot (e.g. the pages of a tiled download). Only the snapshot that refers to them
        is recorded in the manifest, so `latest()` and change detection see one entry per fetch.
        """
        tmp_dir = self._source_dir(source_id) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=tmp_dir, delete=False) as fh:
            fh.write(content)
            tmp = Path(fh.name)
        sha, _ = self._put_object(source_id, tmp, suffix)
        return sha

    def _put_object(self, source_id: str, file: Path, suffix: str) -> tuple[str, Path]:
        sha = sha256_file(file)
        target = object_path(self._source_dir(source_id) / "objects", sha, suffix)
        if target.exists():
            file.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(file, target)
        return sha, target

    def history(self, source_id: str) -> list[Snapshot]:
        manifest = self._manifest(source_id)
        if not manifest.exists():
            return []
        lines = manifest.read_text(encoding="utf-8").splitlines()
        return [Snapshot.from_json(json.loads(line)) for line in lines if line.strip()]

    def latest(self, source_id: str) -> Snapshot | None:
        history = self.history(source_id)
        return history[-1] if history else None

    def latest_good(self, source_id: str) -> Snapshot | None:
        good = [s for s in self.history(source_id) if s.good]
        return good[-1] if good else None

    def mark_good(self, snapshot: Snapshot) -> Snapshot:
        """Record that parse() succeeded on this snapshot, so it can serve as a fallback later."""
        history = self.history(snapshot.source_id)
        updated = replace(snapshot, good=True)
        lines = [
            updated.to_json()
            if (s.sha256, s.fetched_at) == (snapshot.sha256, snapshot.fetched_at)
            else s.to_json()
            for s in history
        ]
        manifest = self._manifest(snapshot.source_id)
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        tmp.replace(manifest)
        return updated
