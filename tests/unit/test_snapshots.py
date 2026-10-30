from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from dclh_pipeline.snapshots import SnapshotStore


def test_save_dedupes_identical_content(store: SnapshotStore) -> None:
    a = store.save("gdos", b"<xml/>", suffix=".xml", fetched_at=datetime(2026, 10, 1, tzinfo=UTC))
    b = store.save("gdos", b"<xml/>", suffix=".xml", fetched_at=datetime(2026, 10, 8, tzinfo=UTC))
    assert a.sha256 == b.sha256
    assert a.path == b.path
    assert a.new_content is True
    assert b.new_content is False
    assert len(store.history("gdos")) == 2
    assert len(list((store.root / "gdos" / "objects").rglob("*.xml"))) == 1


def test_latest_and_latest_good(store: SnapshotStore) -> None:
    assert store.latest("prg") is None
    first = store.save("prg", b"v1")
    store.mark_good(first)
    second = store.save("prg", b"v2")
    latest, good = store.latest("prg"), store.latest_good("prg")
    assert latest is not None and latest.sha256 == second.sha256 and not latest.good
    assert good is not None and good.sha256 == first.sha256 and good.good


def test_snapshot_records_date_and_hash(store: SnapshotStore) -> None:
    snap = store.save("isok", b"abc", url="https://example.org/x", content_type="text/plain")
    assert snap.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert snap.fetched_at.tzinfo is not None
    assert snap.path.read_bytes() == b"abc"
    assert store.history("isok")[0] == snap


def test_manifest_paths_are_relative_to_the_store_root(store: SnapshotStore) -> None:
    store.save("src", b"payload", suffix=".txt")
    line = json.loads((store.root / "src" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert not Path(line["path"]).is_absolute()
    assert line["path"].startswith("src/objects/")


def test_moved_store_still_resolves_paths(store: SnapshotStore, tmp_path: Path) -> None:
    """Host and Docker mount the data folder at different places: the manifest must work at both."""
    store.save("src", b"payload", suffix=".txt")
    moved = tmp_path / "elsewhere"
    store.root.rename(moved)
    snap = SnapshotStore(moved).latest("src")
    assert snap is not None
    assert snap.path.read_bytes() == b"payload"
    assert snap.path.is_relative_to(moved)


def test_legacy_absolute_manifest_is_rebased(store: SnapshotStore) -> None:
    snap = store.save("src", b"payload", suffix=".txt")
    manifest = store.root / "src" / "manifest.jsonl"
    entry = json.loads(manifest.read_text(encoding="utf-8"))
    entry["path"] = (
        "/data/raw/src/objects/" + snap.path.relative_to(store.root / "src" / "objects").as_posix()
    )
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    latest = store.latest("src")
    assert latest is not None
    assert latest.path == snap.path
    store.mark_good(latest)
    assert json.loads(manifest.read_text(encoding="utf-8"))["path"].startswith("src/objects/")


def test_a_compressed_object_is_named_by_its_plain_content_and_stored_once(store: SnapshotStore) -> None:
    body = b"<gml>" + b"parcel " * 1000 + b"</gml>"
    first = store.put_object("egib", body, suffix=".gml", compress=True)
    second = store.put_object("egib", body, suffix=".gml", compress=True)
    assert first == second == hashlib.sha256(body).hexdigest()
    (packed,) = (p for p in (store.root / "egib" / "objects").rglob("*") if p.is_file())
    assert packed.name == f"{first}.gml.gz" and gzip.decompress(packed.read_bytes()) == body
    assert packed.stat().st_size < len(body)


def test_compacting_leaves_other_suffixes_and_an_empty_store_alone(store: SnapshotStore) -> None:
    assert store.compact_objects("egib", ".gml") == (0, 0, 0)
    store.save("egib", b"{}", suffix=".json")
    assert store.compact_objects("egib", ".gml") == (0, 0, 0)
    assert len(list((store.root / "egib" / "objects").rglob("*.json"))) == 1
