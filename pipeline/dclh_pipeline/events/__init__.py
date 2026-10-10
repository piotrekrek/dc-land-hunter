"""Monitoring events (brief section 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    NEW_SITE = "NEW_SITE"
    SCORE_CHANGE = "SCORE_CHANGE"
    ZONING_NEW = "ZONING_NEW"
    ZONING_PROCEDURE = "ZONING_PROCEDURE"
    GRID_CAPACITY_CHANGE = "GRID_CAPACITY_CHANGE"
    PSE_ODB_NEW = "PSE_ODB_NEW"
    PSE_CLOSED_CHANGE = "PSE_CLOSED_CHANGE"
    LAND_OFFER_NEW = "LAND_OFFER_NEW"
    SOURCE_FAILURE = "SOURCE_FAILURE"


@dataclass(frozen=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    source_id: str | None = None
    site_id: str | None = None
    substation_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
