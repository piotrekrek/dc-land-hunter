from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "raw")


@pytest.fixture
def make_client(fake_clock: FakeClock) -> Callable[..., PoliteClient]:
    def factory(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> PoliteClient:
        return PoliteClient(
            user_agent="DCLandHunter/test (+mailto:test@example.org)",
            transport=httpx.MockTransport(handler),
            clock=fake_clock,
            sleep=fake_clock.sleep,
            **kwargs,  # type: ignore[arg-type]
        )

    return factory
