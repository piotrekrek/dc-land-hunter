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
from dclh_pipeline.sources.prg import LAYERS, PrgConnector

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
        "country": feature_collection("A00_Granice_panstwa", "00", "Polska", 500_000, 500_000, 400_000),
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
        assert sorted(archive.namelist()) == sorted(f"{level}.gml" for level in LAYERS)
