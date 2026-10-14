from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.gdos import LAYERS, GdosConnector

ClientFactory = Callable[..., PoliteClient]

SPEC = SourceSpec(id="gdos", name="GDOŚ", used_for="t", plan="mvp", cadence="monthly")


def feature_collection(
    typename: str, code: str, name: str, x0: float, y0: float, side: float = 1000.0
) -> bytes:
    """A minimal WFS 2.0 response with one polygon, in EPSG:2180 (easting, northing). Field names as in the live service."""
    x1, y1 = x0 + side, y0 + side
    ring = f"{x0} {y0} {x1} {y0} {x1} {y1} {x0} {y1} {x0} {y0}"
    layer = typename.split(":")[1]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:GDOS="GDOS" numberMatched="1" numberReturned="1">
  <wfs:member>
    <GDOS:{layer} gml:id="{layer}.1">
      <GDOS:gid>1</GDOS:gid>
      <GDOS:nazwa>{name}</GDOS:nazwa>
      <GDOS:kodinspire>{code}</GDOS:kodinspire>
      <GDOS:geom>
        <gml:MultiSurface srsName="http://www.opengis.net/gml/srs/epsg.xml#2180">
          <gml:surfaceMember><gml:Polygon>
            <gml:exterior><gml:LinearRing><gml:posList>{ring}</gml:posList></gml:LinearRing></gml:exterior>
          </gml:Polygon></gml:surfaceMember>
        </gml:MultiSurface>
      </GDOS:geom>
    </GDOS:{layer}>
  </wfs:member>
</wfs:FeatureCollection>""".encode()


def good_parts() -> dict[str, bytes]:
    return {
        kind: feature_collection(typename, f"PL.{kind}", kind, 500_000 + i * 5_000, 500_000)
        for i, (kind, typename) in enumerate(LAYERS.items())
    }


def bundle(parts: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for kind, content in parts.items():
            archive.writestr(f"{kind}-0000.gml", content)
    return buffer.getvalue()


def save(store: SnapshotStore, content: bytes):  # type: ignore[no-untyped-def]
    return store.save(SPEC.id, content, suffix=".zip")


def test_parse_builds_one_table_in_2180(tmp_path: Path) -> None:
    snapshot = save(SnapshotStore(tmp_path), bundle(good_parts()))
    table = GdosConnector(SPEC).parse(snapshot)
    assert set(table["kind"]) == set(LAYERS)
    assert len(table) == len(LAYERS)
    assert table.crs.to_epsg() == 2180
    assert table.loc[table["kind"] == "reserve", "name"].iloc[0] == "reserve"
    assert table.loc[table["kind"] == "reserve", "code"].iloc[0] == "PL.reserve"


def test_missing_layer_is_reported(tmp_path: Path) -> None:
    parts = good_parts()
    del parts["national_park"]
    snapshot = save(SnapshotStore(tmp_path), bundle(parts))
    with pytest.raises(ValueError, match="national_park"):
        GdosConnector(SPEC).parse(snapshot)


def test_missing_name_field_is_reported(tmp_path: Path) -> None:
    parts = good_parts()
    parts["reserve"] = parts["reserve"].replace(b"nazwa", b"nazwa_x")
    snapshot = save(SnapshotStore(tmp_path), bundle(parts))
    with pytest.raises(ValueError, match="nazwa"):
        GdosConnector(SPEC).parse(snapshot)


def test_swapped_axis_is_rejected(tmp_path: Path) -> None:
    # A feature far outside Poland (a wrong projection or swapped axes) must fail the load, not be scored.
    parts = good_parts()
    parts["reserve"] = feature_collection("GDOS:Rezerwaty", "PL.x", "x", 50_000, 50_000)
    snapshot = save(SnapshotStore(tmp_path), bundle(parts))
    with pytest.raises(ValueError, match="outside Poland"):
        GdosConnector(SPEC).parse(snapshot)


def test_fetch_bundles_each_layer(tmp_path: Path, make_client: ClientFactory) -> None:
    by_type = {
        typename: feature_collection(typename, "PL.x", "x", 500_000, 500_000) for typename in LAYERS.values()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=by_type[request.url.params["typeNames"]])

    store = SnapshotStore(tmp_path)
    snapshot = GdosConnector(SPEC).fetch(make_client(handler), store)
    with zipfile.ZipFile(snapshot.path) as archive:
        assert sorted(archive.namelist()) == sorted(f"{kind}-0000.gml" for kind in LAYERS)
    assert set(GdosConnector(SPEC).parse(snapshot)["kind"]) == set(LAYERS)


def test_fetch_pages_until_number_matched(tmp_path: Path, make_client: ClientFactory) -> None:
    total = 2_152  # the reserves layer on 14.10.2026 needs three pages of 1000
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        start = int(request.url.params["startIndex"])
        count = min(1000, total - start)
        seen.append(request.url.params["startIndex"])
        body = f'<wfs:FeatureCollection numberMatched="{total}" numberReturned="{count}"></wfs:FeatureCollection>'
        return httpx.Response(200, content=body.encode())

    GdosConnector(SPEC)._fetch_layer(make_client(handler), _archive(tmp_path), "reserve", LAYERS["reserve"])
    assert seen == ["0", "1000", "2000"]


def test_fetch_rejects_a_layer_cut_short(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        # claims 2,152 features, then returns an empty page
        body = '<wfs:FeatureCollection numberMatched="2152" numberReturned="0"></wfs:FeatureCollection>'
        return httpx.Response(200, content=body.encode())

    with pytest.raises(ValueError, match="got 0 of 2152"):
        GdosConnector(SPEC)._fetch_layer(
            make_client(handler), _archive(tmp_path), "reserve", LAYERS["reserve"]
        )


def test_wfs_exception_is_raised_even_on_http_200(tmp_path: Path, make_client: ClientFactory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"<ows:ExceptionReport>no such layer</ows:ExceptionReport>")

    with pytest.raises(ValueError, match="exception"):
        GdosConnector(SPEC)._fetch_layer(
            make_client(handler), _archive(tmp_path), "reserve", LAYERS["reserve"]
        )


def _archive(tmp_path: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(), "w", compression=zipfile.ZIP_DEFLATED)


def test_powiat_search_needs_a_saved_gdos_snapshot(tmp_path: Path) -> None:
    # Without gdos the protected-area exclusion would silently never apply, so the search must refuse to run.
    from dclh_pipeline.powiat_search import search_powiat
    from dclh_pipeline.settings import Settings

    settings = Settings(data_dir=tmp_path, contact_email="owner@example.org")
    store = SnapshotStore(settings.raw_dir)
    store.save("prg", b"placeholder", suffix=".zip")
    store.save("osm_power", b"placeholder", suffix=".pbf")
    with pytest.raises(ValueError, match="gdos"):
        search_powiat("1421", settings)
