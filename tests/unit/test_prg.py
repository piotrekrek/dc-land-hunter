from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import box

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.prg import LAYERS, PrgConnector, _check_axis_order

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="prg", name="PRG", used_for="t", plan="mvp", cadence="monthly")


def feature_collection(layer: str, code: str, name: str, x0: float, y0: float, side: float = 1000.0) -> bytes:
    """A minimal WFS 2.0 response with one polygon. Coordinates are easting, northing (x, y)."""
    x1, y1 = x0 + side, y0 + side
    ring = f"{x0} {y0} {x1} {y0} {x1} {y1} {x0} {y1} {x0} {y0}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:ms="http://mapserver.gis.umn.edu/mapserver">
  <wfs:member>
    <ms:{layer} gml:id="{layer}.1">
      <ms:jpt_kod_je>{code}</ms:jpt_kod_je>
      <ms:jpt_nazwa_>{name}</ms:jpt_nazwa_>
      <ms:geometry>
        <gml:Polygon srsName="http://www.opengis.net/gml/srs/epsg.xml#2180">
          <gml:exterior><gml:LinearRing><gml:posList>{ring}</gml:posList></gml:LinearRing></gml:exterior>
        </gml:Polygon>
      </ms:geometry>
    </ms:{layer}>
  </wfs:member>
</wfs:FeatureCollection>""".encode()


def bundle(parts: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for level, content in parts.items():
            archive.writestr(f"{level}.gml", content)
    return buffer.getvalue()


def good_parts() -> dict[str, bytes]:
    return {
        "country": feature_collection("A00_Granice_panstwa", "00", "Polska", 400_000, 400_000, 300_000),
        "voivodeship": feature_collection("A01_Granice_wojewodztw", "14", "mazowieckie", 600_000, 600_000),
        "powiat": feature_collection("A02_Granice_powiatow", "1465", "warszawski zachodni", 610_000, 610_000),
        "gmina": feature_collection("A03_Granice_gmin", "146504", "Ożarów Mazowiecki", 620_000, 620_000),
    }


def save_bundle(store: SnapshotStore, content: bytes) -> object:
    return store.save(SPEC.id, content, suffix=".zip")


def test_parse_builds_one_table_in_2180(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    snapshot = save_bundle(store, bundle(good_parts()))
    table = PrgConnector(SPEC).parse(snapshot)  # type: ignore[arg-type]
    assert list(table["level"]) == list(LAYERS)
    assert list(table["teryt"]) == ["00", "14", "1465", "146504"]
    assert table.crs.to_epsg() == 2180
    assert table.loc[table["level"] == "gmina", "name"].item() == "Ożarów Mazowiecki"


def test_missing_name_field_is_reported(tmp_path: Path) -> None:
    parts = good_parts()
    parts["voivodeship"] = parts["voivodeship"].replace(b"jpt_nazwa_", b"nazwa_other")
    store = SnapshotStore(tmp_path)
    snapshot = save_bundle(store, bundle(parts))
    with pytest.raises(ValueError, match="jpt_nazwa_"):
        PrgConnector(SPEC).parse(snapshot)  # type: ignore[arg-type]


def test_swapped_axis_is_rejected(tmp_path: Path) -> None:
    parts = good_parts()
    # Degrees (lon 21, lat 52) instead of metres: what a wrong axis or CRS handling looks like.
    parts["country"] = feature_collection("A00_Granice_panstwa", "00", "Polska", 21.0, 52.0, 1.0)
    store = SnapshotStore(tmp_path)
    snapshot = save_bundle(store, bundle(parts))
    with pytest.raises(ValueError, match="outside Poland"):
        PrgConnector(SPEC).parse(snapshot)  # type: ignore[arg-type]


def test_fetch_bundles_each_layer(tmp_path: Path, make_client: ClientFactory) -> None:
    by_layer = {v: feature_collection(v.split(":")[1], "00", "x", 500_000, 500_000) for v in LAYERS.values()}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=by_layer[request.url.params["typeNames"]])

    store = SnapshotStore(tmp_path)
    snapshot = PrgConnector(SPEC).fetch(make_client(handler), store)
    with zipfile.ZipFile(snapshot.path) as archive:
        assert sorted(archive.namelist()) == sorted(f"{level}-0000.gml" for level in LAYERS)
    table = PrgConnector(SPEC).parse(snapshot)
    assert set(table["level"]) == set(LAYERS)


def paged_response(request: httpx.Request, total: int) -> httpx.Response:
    start, count = int(request.url.params["startIndex"]), int(request.url.params["count"])
    n = max(0, min(count, total - start))
    members = "".join(
        feature_collection("A03_Granice_gmin", f"{start + i:06d}", "x", 500_000, 500_000)
        .decode()
        .split("<wfs:member>")[1]
        .split("</wfs:member>")[0]
        .join(["<wfs:member>", "</wfs:member>"])
        for i in range(n)
    )
    body = (
        f'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" '
        f'xmlns:ms="http://mapserver.gis.umn.edu/mapserver" numberMatched="{total}" numberReturned="{n}">{members}'
        "</wfs:FeatureCollection>"
    )
    return httpx.Response(200, content=body.encode())


def test_fetch_pages_until_number_matched(tmp_path: Path, make_client: ClientFactory) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(int(request.url.params["startIndex"]))
        return paged_response(request, 2500)

    snapshot = PrgConnector(SPEC).fetch(make_client(handler), SnapshotStore(tmp_path))
    assert calls[:3] == [0, 1000, 2000]
    with zipfile.ZipFile(snapshot.path) as archive:
        assert "gmina-0002.gml" in archive.namelist()


def test_fetch_rejects_truncated_layer(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if int(request.url.params["startIndex"]) > 0:
            empty = b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="2500" numberReturned="0"/>'
            return httpx.Response(200, content=empty)
        return paged_response(request, 2500)

    with pytest.raises(ValueError, match="empty page"):
        PrgConnector(SPEC).fetch(make_client(handler), SnapshotStore(tmp_path))


def poznan_table(x: float, y: float) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"level": ["gmina"], "teryt": ["306401"], "name": ["Poznań"]},
        geometry=[box(x - 500, y - 500, x + 500, y + 500)],
        crs="EPSG:2180",
    )


def test_mirrored_poznan_is_rejected() -> None:
    # (505 km, 360 km) is inside the bounding box, but it is Poznań mirrored across the diagonal.
    with pytest.raises(ValueError, match="Poznań"):
        _check_axis_order(poznan_table(505_000, 360_000))


def test_poznan_in_place_is_accepted() -> None:
    _check_axis_order(poznan_table(360_000, 505_000))


def test_upper_case_field_names_from_the_live_service_are_read(tmp_path: Path) -> None:
    """The live WFS returns JPT_KOD_JE / JPT_NAZWA_ (checked on the VPS, 11.10.2026)."""
    upper = {
        layer: content.replace(b"jpt_kod_je", b"JPT_KOD_JE").replace(b"jpt_nazwa_", b"JPT_NAZWA_")
        for layer, content in good_parts().items()
    }
    store = SnapshotStore(tmp_path)
    table = PrgConnector(SPEC).parse(save_bundle(store, bundle(upper)))  # type: ignore[arg-type]
    assert list(table["teryt"]) == ["00", "14", "1465", "146504"]
    assert table.loc[table["level"] == "gmina", "name"].item() == "Ożarów Mazowiecki"


def test_measured_live_extent_is_inside_the_bounds() -> None:
    """The extent the live PRG download had on 11.10.2026 must pass the bounds check."""
    from dclh_pipeline.sources.prg import POLAND_BOUNDS

    x0, y0, x1, y1 = POLAND_BOUNDS
    live = (171_699.0, 133_223.0, 861_896.0, 797_228.0)
    assert x0 < live[0] and y0 < live[1] and live[2] < x1 and live[3] < y1
