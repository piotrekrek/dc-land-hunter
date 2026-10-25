"""Orthophoto under the survey drawing of a parcel check (PLAN.md, "Next round", step 4).

GUGiK's Geoportal serves the national orthophoto as a WMS that can answer in EPSG:2180, the CRS the sheet is
drawn in, so one request returns a JPEG for exactly the sheet's box and the drawing lays over it without any
reprojection. The service is free of charge (its capabilities say "Brak opłat"; checked 25.10.2026).
The request is made from the server and cached, so the browser never talks to the Geoportal.

WMS 1.1.1 on purpose: 1.3.0 swaps the axis order for EPSG:2180 (northing first), a classic way to get a photo
of the wrong place.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from dclh_pipeline.response_cache import ResponseCache

log = logging.getLogger(__name__)

ORTHOPHOTO_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution"
MAX_SIDE_PX = 1100  # the longest side of the picture; the service allows 4096
MAX_SIDE_M = 6_000.0  # a sheet is a parcel plus a margin; anything bigger is not a sheet


@dataclass(frozen=True)
class OrthophotoRequest:
    bbox: tuple[float, float, float, float]  # EPSG:2180: x0, y0, x1, y1
    width_px: int
    height_px: int

    def params(self) -> dict[str, str]:
        x0, y0, x1, y1 = self.bbox
        return {
            "SERVICE": "WMS",
            "REQUEST": "GetMap",
            "VERSION": "1.1.1",
            "LAYERS": "Raster",
            "STYLES": "",
            "SRS": "EPSG:2180",
            "BBOX": f"{x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f}",
            "WIDTH": str(self.width_px),
            "HEIGHT": str(self.height_px),
            "FORMAT": "image/jpeg",
        }


def request_for_sheet(
    origin: tuple[float, float], bbox_m: tuple[float, float, float, float]
) -> OrthophotoRequest:
    """The request for a sheet: `origin` is its zero in EPSG:2180, `bbox_m` its box in metres around it."""
    ox, oy = origin
    x0, y0, x1, y1 = ox + bbox_m[0], oy + bbox_m[1], ox + bbox_m[2], oy + bbox_m[3]
    width, height = x1 - x0, y1 - y0
    if not (0 < width <= MAX_SIDE_M and 0 < height <= MAX_SIDE_M):
        raise ValueError(f"sheet of {width:.0f} x {height:.0f} m is not a plausible sheet")
    scale = MAX_SIDE_PX / max(width, height)
    return OrthophotoRequest((x0, y0, x1, y1), max(1, round(width * scale)), max(1, round(height * scale)))


def looks_like_jpeg(body: bytes) -> bool:
    """The service answers a failed request with an XML error and a 200: check the bytes, not the status."""
    return body[:3] == b"\xff\xd8\xff"


class OrthophotoUnavailable(RuntimeError):
    """The Geoportal gave no picture after every attempt."""


def fetch_orthophoto(
    wanted: OrthophotoRequest,
    *,
    user_agent: str,
    cache: ResponseCache | None = None,
    attempts: int = 5,
    pause_s: float = 0.4,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """The JPEG for `wanted`, from the cache or the Geoportal.

    The service sits behind a pool where one node in about four answers an empty 404 to a valid request (seen
    25.10.2026), and its session cookie then keeps a client on that node. So every attempt is a new client
    with no cookies, and an empty or non-JPEG answer is asked again.
    """
    params = wanted.params()
    key = ResponseCache.key("GET", ORTHOPHOTO_URL, params)
    if (
        cache is not None
        and (hit := cache.get(key, ORTHOPHOTO_URL)) is not None
        and looks_like_jpeg(hit.content)
    ):
        return hit.content
    last = "no attempt"
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause_s)
        try:
            with httpx.Client(
                headers={"User-Agent": user_agent}, timeout=30.0, transport=transport
            ) as client:
                response = client.get(ORTHOPHOTO_URL, params=params)
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
            continue
        if response.status_code == 200 and looks_like_jpeg(response.content):
            if cache is not None:
                cache.put(key, response)
            return response.content
        last = f"HTTP {response.status_code}, {len(response.content)} bytes"
    log.warning("orthophoto unavailable after %d attempts (%s)", attempts, last)
    raise OrthophotoUnavailable(last)
