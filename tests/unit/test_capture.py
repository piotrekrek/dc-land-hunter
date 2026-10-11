"""Sample capture: requests, files and metadata. The mock transport stands in for the live services."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from dclh_pipeline.capture import SAMPLE_PARCELS, Place, capture, default_out_dir
from dclh_pipeline.http import PoliteClient
from tests.conftest import FIXTURES

EGIB_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/EGIB/WFS/UslugaZbiorcza"
KONIN = Place(name="CGE Konin", lon=18.2514, lat=52.2360, radius_m=300)


def handler(seen: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)  # no rules
        seen.append(request)
        if request.url.host == "mapy.geoportal.gov.pl":
            return httpx.Response(200, content=(FIXTURES / "egib" / "tile_a_page1.gml").read_bytes())
        return httpx.Response(
            200, content=b"0\n146510_8.0502.55|mazowieckie|powiat Warszawa|Warszawa (miasto)\n"
        )

    return respond


def test_place_bbox_is_a_square_around_the_centre_in_2180() -> None:
    x, y = KONIN.centre
    x0, y0, x1, y1 = KONIN.bbox
    assert (x1 - x0) == (y1 - y0) == 600
    assert x0 < x < x1 and y0 < y < y1
    # Konin is about 50 km west of the 19°E central meridian: x a little under 450 000 (500 000 is the meridian).
    assert 440_000 < x < 460_000 and 480_000 < y < 490_000  # false northing is -5 300 000 m


def test_capture_writes_both_responses_and_metadata(
    tmp_path: Path, make_client: Callable[..., PoliteClient]
) -> None:
    seen: list[httpx.Request] = []
    with make_client(handler(seen)) as client:
        meta = capture(
            client,
            egib_url=EGIB_URL,
            place=KONIN,
            out_dir=tmp_path,
            now=datetime(2026, 10, 11, tzinfo=UTC),
        )

    egib_req = next(r for r in seen if r.url.host == "mapy.geoportal.gov.pl")
    assert egib_req.url.params["typeNames"] == "ms:dzialki"
    assert egib_req.url.params["count"] == str(SAMPLE_PARCELS)
    assert egib_req.url.params["startIndex"] == "0"
    uldk_req = next(r for r in seen if r.url.host == "uldk.gugik.gov.pl")
    assert uldk_req.url.params["request"] == "GetParcelByXY"
    assert uldk_req.url.params["xy"].endswith(",2180")

    assert (tmp_path / "egib_getfeature.gml").read_bytes().startswith(b"<?xml")
    assert (tmp_path / "uldk_getparcelbyxy.txt").read_bytes().startswith(b"0\n")
    saved = json.loads((tmp_path / "capture.json").read_text(encoding="utf-8"))
    assert saved == meta
    assert [r["source_id"] for r in saved["responses"]] == ["egib", "uldk"]
    assert all(r["status"] == 200 and len(r["sha256"]) == 64 for r in saved["responses"])
    assert saved["fetched_at"].startswith("2026-10-11")


def test_default_folder_is_named_after_the_place(tmp_path: Path) -> None:
    assert default_out_dir(tmp_path, KONIN) == tmp_path / "tests" / "fixtures" / "live" / "cge-konin"
