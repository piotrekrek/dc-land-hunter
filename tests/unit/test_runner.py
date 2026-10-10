from __future__ import annotations

from collections.abc import Callable

import httpx
import pandas as pd
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.events import EventType
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.runner import RunStatus, run_source, run_sources

SPEC = SourceSpec(id="fake", name="Fake", used_for="tests", plan="mvp", cadence="weekly")


class FakeSource(Source):
    id = "fake"

    def __init__(self, payload: bytes | Exception) -> None:
        super().__init__(SPEC)
        self.payload = payload

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        if isinstance(self.payload, Exception):
            raise self.payload
        return store.save(self.id, self.payload, suffix=".csv")

    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        text = snapshot.path.read_text(encoding="utf-8")
        if not text.startswith("a,b"):
            raise ValueError("unexpected header")
        return pd.read_csv(snapshot.path)


@pytest.fixture
def client(make_client: Callable[..., PoliteClient]) -> PoliteClient:
    return make_client(lambda request: httpx.Response(404))


def test_ok_run_marks_snapshot_good(client: PoliteClient, store: SnapshotStore) -> None:
    result = run_source(FakeSource(b"a,b\n1,2\n"), client, store)
    assert result.status is RunStatus.OK
    assert result.row_count == 1
    assert result.events == []
    good = store.latest_good("fake")
    assert good is not None and good.sha256 == result.snapshot.sha256  # type: ignore[union-attr]


def test_fetch_failure_falls_back_to_last_good(client: PoliteClient, store: SnapshotStore) -> None:
    run_source(FakeSource(b"a,b\n1,2\n3,4\n"), client, store)
    result = run_source(FakeSource(httpx.ConnectTimeout("timeout")), client, store)
    assert result.status is RunStatus.STALE
    assert result.row_count == 2
    assert [e.type for e in result.events] == [EventType.SOURCE_FAILURE]
    assert "ConnectTimeout" in (result.error or "")


def test_parse_failure_does_not_poison_fallback(client: PoliteClient, store: SnapshotStore) -> None:
    run_source(FakeSource(b"a,b\n1,2\n"), client, store)
    result = run_source(FakeSource(b"<html>maintenance</html>"), client, store)
    assert result.status is RunStatus.STALE
    assert result.row_count == 1
    latest = store.latest("fake")
    assert latest is not None and not latest.good  # the broken snapshot is kept but never used as fallback


def test_failure_without_history(client: PoliteClient, store: SnapshotStore) -> None:
    result = run_source(FakeSource(RuntimeError("boom")), client, store)
    assert result.status is RunStatus.FAILED
    assert result.table is None
    assert result.events[0].type is EventType.SOURCE_FAILURE


def test_one_failure_never_blocks_the_run(client: PoliteClient, store: SnapshotStore) -> None:
    results = run_sources([FakeSource(RuntimeError("down")), FakeSource(b"a,b\n5,6\n")], client, store)
    assert [r.status for r in results] == [RunStatus.FAILED, RunStatus.OK]


def test_spec_id_must_match() -> None:
    other = SPEC.model_copy(update={"id": "other"})

    class Wrong(FakeSource):
        def __init__(self) -> None:
            Source.__init__(self, other)

    with pytest.raises(ValueError, match="other"):
        Wrong()
