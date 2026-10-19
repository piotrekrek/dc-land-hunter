from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pandas as pd
import pytest
from shapely import box

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.provenance import Confidence
from dclh_pipeline.model.sites import Parcel, cluster_sites
from dclh_pipeline.scoring.types import ZoningKind
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.kimpzp import (
    ERROR,
    NO_PLAN,
    NO_SERVICE,
    PLAN,
    UNPARSED,
    KimpzpConnector,
    getfeatureinfo_params,
    parse_answer,
    sample_points,
    site_points,
    zoning_layer,
)

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="kimpzp", name="KIMPZP", used_for="t", plan="mvp", cadence="weekly")
WARSAW = (
    Path(__file__).parents[1]
    / "fixtures/live/source-verification-2026-10-19/kimpzp_gfi_warszawa_sluzewiec.xml"
)

GEOSERVER_EMPTY = b"""<html><head><title>Geoserver GetFeatureInfo output</title></head>
<style type="text/css">table.featureInfo { border:1px solid #ddd; }</style>
<body>

</body></html>"""

GEOSERVER_ROWS = """<html><body><table class="featureInfo"><caption class="featureInfo">mpzp</caption>
<tr><th>fid</th><th>symbol</th><th>przeznaczenie</th></tr>
<tr><td>mpzp.1</td><td>3PU</td><td>teren produkcji i usług</td></tr>
</table></body></html>""".encode()


def test_warsaw_xml_answer_gives_the_designation_codes() -> None:
    status, codes = parse_answer(WARSAW.read_bytes())
    assert status == PLAN
    assert codes == ["3k U-O/US", "2 KDD"]  # each once, though the answer repeats the rowset


@pytest.mark.parametrize(
    ("body", "status", "codes"),
    [
        ("Brwinów: brak wyniku dla wskazanego obszaru".encode(), NO_PLAN, []),
        ("m. Podkowa Leśna: brak wyniku dla wskazanego obszaru".encode(), NO_PLAN, []),
        (b"Raszyn: brak serwisu dla wskazanego obszaru", NO_SERVICE, []),
        (GEOSERVER_EMPTY, NO_PLAN, []),
        (GEOSERVER_ROWS, PLAN, ["3PU"]),
        (
            b'<?xml version="1.0"?><FeatureInfoResponse><FIELDS OBJECTID="7" SYMBOL="P/U"/></FeatureInfoResponse>',
            PLAN,
            ["P/U"],
        ),
        (b'<?xml version="1.0"?><GetFeatureInfo_Result></GetFeatureInfo_Result>', NO_PLAN, []),
        (b"<html><body><table><tr><th>fid</th></tr><tr><td>1</td></tr></table></body></html>", UNPARSED, []),
        (b"<html><body><img src='legend.png'/>Plan rastrowy</body></html>", UNPARSED, []),
        (b"Something new", UNPARSED, []),
        (b'<?xml version="1.0"?><broken', UNPARSED, []),
    ],
)
def test_parse_answer_tolerates_each_gmina_layout(body: bytes, status: str, codes: list[str]) -> None:
    assert parse_answer(body) == (status, codes)


def test_params_use_wms_111_and_a_lon_lat_bbox() -> None:
    params = getfeatureinfo_params(20.999, 52.18)
    assert params["VERSION"] == "1.1.1"
    assert params["SRS"] == "EPSG:4326"
    assert params["QUERY_LAYERS"] == "plany"
    lon0, lat0, lon1, lat1 = (float(v) for v in str(params["BBOX"]).split(","))
    assert lon0 < 20.999 < lon1 and lat0 < 52.18 < lat1
    assert (params["X"], params["Y"]) == (50, 50)


def test_sample_points_lie_inside_the_site() -> None:
    l_shape = box(0, 0, 1_000, 200).union(box(0, 0, 200, 600))
    points = sample_points(l_shape)
    assert len(points) == 3
    assert all(l_shape.contains(p) for p in points)


def site_at(x0: float, y0: float):  # type: ignore[no-untyped-def]
    return cluster_sites([Parcel(f"p{x0}", None, box(x0, y0, x0 + 400, y0 + 300))])[0]


def test_site_points_are_lon_lat_in_poland() -> None:
    site = site_at(637_000, 486_000)  # Warsaw
    points = site_points([site])
    assert len(points) == 3
    assert {key for key, _, _ in points} == {site.site_id}
    for _, lon, lat in points:
        assert 20.9 < lon < 21.1 and 52.1 < lat < 52.3


def test_fetch_keeps_every_raw_answer_and_survives_a_failing_gmina(
    tmp_path: Path, make_client: ClientFactory
) -> None:
    answers = iter([WARSAW.read_bytes(), b"Nadarzyn: brak wyniku dla wskazanego obszaru"])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request)
        body = next(answers, None)
        return httpx.Response(500) if body is None else httpx.Response(200, content=body)

    connector = KimpzpConnector(SPEC, points=[("a", 20.999, 52.18), ("a", 20.8, 52.1), ("b", 20.7, 52.0)])
    snapshot = connector.fetch(make_client(handler, retries=0), SnapshotStore(tmp_path))
    table = connector.parse(snapshot)
    assert list(table["status"]) == [PLAN, NO_PLAN, ERROR]
    assert table["codes"].iloc[0] == ["3k U-O/US", "2 KDD"]
    assert seen[0].url.params["VERSION"] == "1.1.1"


def test_zoning_layer_has_one_estimated_zone_per_site_with_a_plan() -> None:
    planned, mixed, unplanned = site_at(0, 0), site_at(1_000, 0), site_at(2_000, 0)
    answers = pd.DataFrame(
        {
            "key": [planned.site_id] * 3 + [mixed.site_id] * 3 + [unplanned.site_id] * 3,
            "codes": [["1PU", "2 KDD"], ["1PU"], [], ["MN"], ["MN"], ["P"], [], [], []],
        }
    )
    layer = zoning_layer([planned, mixed, unplanned], answers, None)
    assert layer.source_id == "kimpzp"
    assert layer.confidence is Confidence.ESTIMATED
    assert [
        (kind, geom.equals(site.geom))
        for (kind, geom), site in zip(layer.zones, (planned, mixed), strict=True)
    ] == [
        (ZoningKind.MPZP_PRODUCTION, True),
        (ZoningKind.OTHER, True),
    ]
