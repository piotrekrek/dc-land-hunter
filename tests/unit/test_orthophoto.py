"""The Geoportal orthophoto request for a sheet (pure parts; the endpoint is tested with the checks API)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.orthophoto import (
    MAX_SIDE_PX,
    OrthophotoUnavailable,
    fetch_orthophoto,
    looks_like_jpeg,
    request_for_sheet,
)
from dclh_pipeline.response_cache import ResponseCache


def test_the_request_covers_the_sheet_box_in_2180_with_x_before_y() -> None:
    wanted = request_for_sheet((630_000.0, 484_000.0), (-750.0, -750.0, 750.0, 750.0))
    assert wanted.bbox == (629_250.0, 483_250.0, 630_750.0, 484_750.0)
    params = wanted.params()
    assert params["BBOX"] == "629250.0,483250.0,630750.0,484750.0"  # WMS 1.1.1: x,y order
    assert params["SRS"] == "EPSG:2180" and params["VERSION"] == "1.1.1" and params["FORMAT"] == "image/jpeg"


def test_the_picture_keeps_the_aspect_ratio_and_the_longest_side_is_capped() -> None:
    wanted = request_for_sheet((500_000.0, 400_000.0), (-1_000.0, -500.0, 1_000.0, 500.0))
    assert (wanted.width_px, wanted.height_px) == (MAX_SIDE_PX, MAX_SIDE_PX // 2)
    tall = request_for_sheet((500_000.0, 400_000.0), (-100.0, -400.0, 100.0, 400.0))
    assert tall.height_px == MAX_SIDE_PX and tall.width_px == MAX_SIDE_PX // 4


@pytest.mark.parametrize(
    "bbox", [(0.0, 0.0, 0.0, 10.0), (0.0, 0.0, 10.0, 0.0), (-9_000.0, 0.0, 9_000.0, 10.0)]
)
def test_a_degenerate_or_huge_box_is_refused(bbox: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValueError, match="plausible"):
        request_for_sheet((500_000.0, 400_000.0), bbox)


def test_only_real_jpeg_bytes_count_as_an_image() -> None:
    assert looks_like_jpeg(b"\xff\xd8\xff\xe0abc")
    assert not looks_like_jpeg(b"<?xml version='1.0'?><ServiceExceptionReport/>")
    assert not looks_like_jpeg(b"")


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WANTED = request_for_sheet((630_000.0, 484_000.0), (-750.0, -750.0, 750.0, 750.0))


def pool(answers: list[int], seen: list[httpx.Request]) -> httpx.MockTransport:
    """The Geoportal as seen on 25.10.2026: some answers are an empty 404, and each one sets a node cookie."""
    queue = iter(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = next(queue)
        body = JPEG if status == 200 else b""
        return httpx.Response(status, content=body, headers={"set-cookie": "node=bad; Path=/"})

    return httpx.MockTransport(handler)


def test_an_empty_404_is_asked_again_with_no_cookie_so_the_pool_can_pick_another_node() -> None:
    seen: list[httpx.Request] = []
    picture = fetch_orthophoto(
        WANTED, user_agent="dclh-test", pause_s=0, transport=pool([404, 404, 200], seen)
    )
    assert picture == JPEG and len(seen) == 3
    assert all("cookie" not in r.headers for r in seen)
    assert seen[0].headers["user-agent"] == "dclh-test"
    assert seen[0].url.params["BBOX"] == "629250.0,483250.0,630750.0,484750.0"


def test_a_200_that_is_not_a_picture_counts_as_a_failure() -> None:
    def xml(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<ServiceExceptionReport/>")

    with pytest.raises(OrthophotoUnavailable):
        fetch_orthophoto(WANTED, user_agent="t", attempts=2, pause_s=0, transport=httpx.MockTransport(xml))


def test_it_gives_up_after_the_attempts_it_was_given() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(OrthophotoUnavailable, match="HTTP 404"):
        fetch_orthophoto(WANTED, user_agent="t", attempts=3, pause_s=0, transport=pool([404] * 3, seen))
    assert len(seen) == 3


def test_a_network_error_is_retried_too() -> None:
    calls = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=JPEG)

    assert fetch_orthophoto(WANTED, user_agent="t", pause_s=0, transport=httpx.MockTransport(flaky)) == JPEG


def test_a_picture_is_kept_so_the_second_request_does_not_leave_the_server(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, timedelta(days=1))
    seen: list[httpx.Request] = []
    first = fetch_orthophoto(WANTED, user_agent="t", cache=cache, pause_s=0, transport=pool([200], seen))
    again = fetch_orthophoto(WANTED, user_agent="t", cache=cache, pause_s=0, transport=pool([], seen))
    assert first == again == JPEG and len(seen) == 1
