"""`dclh kw-probe`: KW number coverage on canned ULDK and KIEG answers. The real services need a machine in Poland."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from dclh_pipeline import kw_probe
from dclh_pipeline.http import PoliteClient

ClientFactory = Callable[..., PoliteClient]

# An L-shaped parcel: its centroid (about 4, 4) lies outside it, so the probe must not use the centroid.
ULDK_OK = "0\nSRID=2180;POLYGON((0 0,10 0,10 2,2 2,2 10,0 10,0 0))\n"
KIEG_WITH_KW = "<html><table><tr><td>Numer KW</td><td>wa1p/00012345/6</td></tr></table></html>"
KIEG_WITHOUT_KW = (
    "<html><table><tr><td>Identyfikator działki</td><td>142104_2.0013.562/2</td></tr></table></html>"
)


def _handler(
    kieg: Callable[[httpx.Request], httpx.Response], uldk: str = ULDK_OK
) -> Callable[..., httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "uldk.gugik.gov.pl":
            return httpx.Response(200, text=uldk)
        return kieg(request)

    return handler


def test_find_kw_normalises_and_deduplicates() -> None:
    text = "KW: WA1P/00012345/6, again wa1p / 00012345 / 6 and PO1P/00098765/1; not KW: 12/2024/3"
    assert kw_probe.find_kw(text) == ["WA1P/00012345/6", "PO1P/00098765/1"]


def test_probe_finds_kw_and_queries_a_point_inside_the_parcel(make_client: ClientFactory) -> None:
    seen: list[dict[str, str]] = []

    def kieg(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text=KIEG_WITH_KW)

    result = kw_probe.probe_parcel(make_client(_handler(kieg)), "142104_2.0013.562/2", "s1")
    assert (result.status, result.kw) == (kw_probe.FOUND, "WA1P/00012345/6")
    params = seen[0]
    assert (params["version"], params["srs"], params["layers"]) == ("1.1.1", "EPSG:2180", "dzialki")
    minx, miny, maxx, maxy = (float(v) for v in params["bbox"].split(","))
    x, y = (minx + maxx) / 2, (miny + maxy) / 2
    assert (0 <= x <= 2 and 0 <= y <= 10) or (
        0 <= x <= 10 and 0 <= y <= 2
    )  # inside the L, not at its centroid


def test_answer_without_kw_falls_back_across_formats(make_client: ClientFactory) -> None:
    formats: list[str] = []

    def kieg(request: httpx.Request) -> httpx.Response:
        formats.append(request.url.params["info_format"])
        if request.url.params["info_format"] == "text/html":
            return httpx.Response(200, text="<ServiceException>unsupported</ServiceException>")
        return httpx.Response(200, text=KIEG_WITHOUT_KW)

    result = kw_probe.probe_parcel(make_client(_handler(kieg)), "142104_2.0013.562/2", "s1")
    assert result.status == kw_probe.NO_KW
    assert formats == ["text/html", "text/plain"]
    assert "142104_2.0013.562/2" in result.sample


def test_empty_answer_and_uldk_error_are_rows_not_exceptions(make_client: ClientFactory) -> None:
    empty = kw_probe.probe_parcel(
        make_client(_handler(lambda _r: httpx.Response(200, text=""))), "142104_2.0013.1", "s1"
    )
    assert empty.status == kw_probe.EMPTY
    failed = kw_probe.probe_parcel(
        make_client(_handler(lambda _r: httpx.Response(200, text=KIEG_WITH_KW), uldk="-1 brak wyników\n")),
        "142104_2.0013.2",
        "s1",
    )
    assert failed.status == kw_probe.ERROR
    assert "ULDK" in failed.note


def test_report_counts_per_status_powiat_and_site() -> None:
    results = [
        kw_probe.ParcelResult("142104_2.0013.1", "s1", kw_probe.FOUND, kw="WA1P/00012345/6", sample="x"),
        kw_probe.ParcelResult("142104_2.0013.2", "s1", kw_probe.NO_KW, sample="y"),
        kw_probe.ParcelResult("306201_1.0002.3", "s2", kw_probe.EMPTY),
        kw_probe.ParcelResult("306201_1.0002.4", "s2", kw_probe.ERROR, note="ValueError: ULDK"),
    ]
    report = kw_probe.render_report(results, sites=2, stamp="2026-10-14 12:00 UTC")
    assert "| kw | 1 | 25% |" in report
    assert "| 1421 | 2 | 1 (50%) | 1 | 0 | 0 |" in report
    assert "| 3062 | 2 | 0 (0%) | 0 | 1 | 1 |" in report
    assert "Sites with at least one KW number: 1 of 2" in report
