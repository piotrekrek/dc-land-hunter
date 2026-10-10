"""Provenance carried by every derived value (brief section 4: source_id, as_of, confidence)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Confidence(StrEnum):
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    MISSING = "missing"


@dataclass(frozen=True)
class Provenance:
    source_id: str
    as_of: datetime | None
    confidence: Confidence
