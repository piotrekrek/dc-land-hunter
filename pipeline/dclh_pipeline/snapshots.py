"""Content-addressed store for raw source snapshots.

Layout under `data/raw/<source_id>/`:
- `objects/<sha256[:2]>/<sha256><suffix>`: raw bytes, stored once per distinct content.
- `manifest.jsonl`: one line per fetch (date, hash, URL, whether parse succeeded).

Manifest paths are relative to the store root (`<source_id>/objects/...`), so the host and the containers
can mount the data folder at different places and still read the same manifest. Older manifests with absolute
paths are rebased onto the root when read, and rewritten as relative on the next `mark_good`.

A fetch that returns identical bytes adds a manifest line (so `as_of` moves forward) but no new object.

An object can be stored gzipped as `<sha256><suffix>.gz`. The name keeps the hash of the uncompressed bytes,
so identical content is still stored once and a bundle that names `<sha256><suffix>` finds either form
(`stored_object`).
"""

from __future__ import annotations

import gzip
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

    def to_json(self, root: Path) -> dict[str, Any]:
        """Manifest form. A path inside `root` is stored relative to it; anything else stays as it is."""
        data = asdict(self)
        data["fetched_at"] = self.fetched_at.isoformat()
        data["path"] = (
            self.path.relative_to(root).as_posix() if self.path.is_relative_to(root) else str(self.path)
        )
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any], root: Path) -> Snapshot:
        return cls(
            **{
                **data,
                "fetched_at": datetime.fromisoformat(data["fetched_at"]),
                "path": resolve_path(data["path"], root, data["source_id"]),
            }
        )


def resolve_path(stored: str, root: Path, source_id: str) -> Path:
    """Turn a manifest path into a real one under `root`.

    Relative paths are joined to `root`. An absolute path comes from an older manifest written on another
    mount point (host vs Docker): take the part from `<source_id>/` on and put it under `root`.
    """
    path = Path(stored)
    if not path.is_absolute():
        return root / path
    parts = path.parts
    if source_id in parts:
        return root.joinpath(*parts[len(parts) - 1 - parts[::-1].index(source_id) :])
    return path


def object_path(objects_dir: Path, sha: str, suffix: str) -> Path:
    """Where an object lives: `<objects_dir>/<sha[:2]>/<sha><suffix>`."""
    return objects_dir / sha[:2] / f"{sha}{suffix}"


def stored_object(objects_dir: Path, sha: str, suffix: str) -> Path:
    """The object as it is on disk: the plain file, or its gzipped form when only that exists."""
    plain = object_path(objects_dir, sha, suffix)
    packed = plain.with_name(plain.name + ".gz")
    return packed if not plain.exists() and packed.exists() else plain


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
            fh.write(json.dumps(snap.to_json(self.root), ensure_ascii=False) + "\n")
        return snap

    def put_object(
        self, source_id: str, content: bytes, *, suffix: str = ".bin", compress: bool = False
    ) -> str:
        """Store raw bytes as an object without a manifest line; returns the sha256.

        For parts of a snapshot (e.g. the pages of a tiled download). Only the snapshot that refers to them
        is recorded in the manifest, so `latest()` and change detection see one entry per fetch.
        With `compress`, the bytes are kept gzipped as `<sha256><suffix>.gz`; the sha256 is of `content`.
        """
        if compress:
            sha = hashlib.sha256(content).hexdigest()
            objects_dir = self._source_dir(source_id) / "objects"
            if not stored_object(objects_dir, sha, suffix).exists():
                target = object_path(objects_dir, sha, suffix + ".gz")
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_bytes(gzip.compress(content, mtime=0))
                tmp.replace(target)
            return sha
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

    def compact_objects(self, source_id: str, suffix: str) -> tuple[int, int, int]:
        """Gzip every plain `<suffix>` object of a source in place; returns (files, bytes before, after).

        The gzipped file is written next to the plain one and the plain one is removed only after that, so an
        interrupted run leaves every object readable. Run it while no job reads this source.
        """
        files = before = after = 0
        for plain in sorted((self._source_dir(source_id) / "objects").glob(f"*/*{suffix}")):
            packed = plain.with_name(plain.name + ".gz")
            if not packed.exists():
                tmp = packed.with_name(packed.name + ".tmp")
                tmp.write_bytes(gzip.compress(plain.read_bytes(), mtime=0))
                tmp.replace(packed)
            files += 1
            before += plain.stat().st_size
            after += packed.stat().st_size
            plain.unlink()
        return files, before, after

    def history(self, source_id: str) -> list[Snapshot]:
        manifest = self._manifest(source_id)
        if not manifest.exists():
            return []
        lines = manifest.read_text(encoding="utf-8").splitlines()
        return [Snapshot.from_json(json.loads(line), self.root) for line in lines if line.strip()]

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
            updated.to_json(self.root)
            if (s.sha256, s.fetched_at) == (snapshot.sha256, snapshot.fetched_at)
            else s.to_json(self.root)
            for s in history
        ]
        manifest = self._manifest(snapshot.source_id)
        tmp = manifest.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
        tmp.replace(manifest)
        return updated
