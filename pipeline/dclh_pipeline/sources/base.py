"""Connector contract: `fetch()` stores a raw snapshot, `parse()` turns it into a normalised table."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import pandas as pd

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore


class Source(ABC):
    """Base class for every connector.

    Subclasses set `id` to the source id from `config/sources.yaml`. `parse()` must be a pure function of the
    snapshot so that it can re-run on the last good snapshot when a fresh fetch fails.
    """

    id: ClassVar[str]

    def __init__(self, spec: SourceSpec) -> None:
        if spec.id != self.id:
            raise ValueError(f"spec {spec.id!r} given to connector {self.id!r}")
        self.spec = spec

    @abstractmethod
    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        """Download the raw data and save it with `store.save()` / `store.save_file()`."""

    @abstractmethod
    def parse(self, snapshot: Snapshot) -> pd.DataFrame:
        """Return a normalised table (a GeoDataFrame in EPSG:2180 for spatial sources)."""
