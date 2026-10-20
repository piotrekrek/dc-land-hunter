"""Keyset cursors for the sites list.

A cursor holds the sort value and site_id of the last row of a page, so the next page starts just after
that row. Unlike an offset, it does not shift when a scoring run inserts or removes rows.
The cursor is opaque to the client. It is not signed: it only chooses a position inside the same
whitelisted, parameterised query, so tampering can change which page is shown but nothing else.
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass
from typing import Any


class CursorError(ValueError):
    """The cursor is malformed, or it was made for another sort or order."""


@dataclass(frozen=True)
class SiteCursor:
    value: float
    site_id: str


def encode_cursor(sort: str, order: str, value: float, site_id: str) -> str:
    payload = {"s": sort, "o": order, "v": value, "id": site_id}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, sort: str, order: str) -> SiteCursor:
    padded = token + "=" * (-len(token) % 4)
    try:
        payload: Any = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except ValueError as exc:  # binascii.Error, JSONDecodeError and UnicodeError are all ValueErrors
        raise CursorError("Nieprawidłowy kursor strony.") from exc
    if not isinstance(payload, dict) or payload.get("s") != sort or payload.get("o") != order:
        raise CursorError("Kursor nie pasuje do wybranego sortowania.")
    value, site_id = payload.get("v"), payload.get("id")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise CursorError("Nieprawidłowy kursor strony.")
    if not isinstance(site_id, str):
        raise CursorError("Nieprawidłowy kursor strony.")
    return SiteCursor(value=float(value), site_id=site_id)
