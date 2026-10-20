"""Keyset cursors for the sites list: encoding, and rejecting cursors that do not fit the request."""

from __future__ import annotations

import base64
import json

import pytest

from dclh_api.pagination import CursorError, SiteCursor, decode_cursor, encode_cursor


def _raw(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_cursor_round_trips_value_and_site_id() -> None:
    token = encode_cursor("score", "desc", 81.4, "site-a1b2")
    assert decode_cursor(token, "score", "desc") == SiteCursor(value=81.4, site_id="site-a1b2")


def test_cursor_keeps_float_precision() -> None:
    value = 0.1 + 0.2  # 0.30000000000000004: a rounded value would drop the row that ties with it
    token = encode_cursor("area", "asc", value, "site-x")
    assert decode_cursor(token, "area", "asc").value == value


def test_cursor_is_url_safe_and_unpadded() -> None:
    token = encode_cursor("mw", "desc", 12.0, "site-with-some-length")
    assert "=" not in token and "+" not in token and "/" not in token


def test_cursor_for_another_sort_or_order_is_rejected() -> None:
    token = encode_cursor("score", "desc", 50.0, "site-a")
    with pytest.raises(CursorError):
        decode_cursor(token, "area", "desc")
    with pytest.raises(CursorError):
        decode_cursor(token, "score", "asc")


@pytest.mark.parametrize(
    "token",
    [
        "!!!not-base64!!!",
        _raw([1, 2, 3]),  # not an object
        _raw({"s": "score", "o": "desc", "v": "81", "id": "site-a"}),  # value is text
        _raw({"s": "score", "o": "desc", "v": True, "id": "site-a"}),  # bool is an int in Python
        _raw({"s": "score", "o": "desc", "v": 1.0, "id": 7}),  # id is not text
        _raw({"s": "score", "o": "desc", "id": "site-a"}),  # value missing
        _raw({"s": "score", "o": "desc", "v": None, "id": "site-a"}),  # value is null
        "",
    ],
)
def test_malformed_cursor_is_rejected(token: str) -> None:
    with pytest.raises(CursorError):
        decode_cursor(token, "score", "desc")


def test_non_finite_value_is_rejected() -> None:
    # json.loads accepts NaN and Infinity, which would make every comparison in the keyset false.
    token = _raw({"s": "score", "o": "desc", "v": float("nan"), "id": "site-a"})
    with pytest.raises(CursorError):
        decode_cursor(token, "score", "desc")
