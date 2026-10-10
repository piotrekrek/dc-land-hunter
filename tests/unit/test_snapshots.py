from __future__ import annotations

from datetime import UTC, datetime

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
