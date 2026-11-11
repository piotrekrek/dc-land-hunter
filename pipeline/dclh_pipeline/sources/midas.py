"""PIG-PIB MIDAS: mining areas, mining terrains and documented deposits for one search area, from the CBDG
ArcGIS MapServer.

Feeds three constraint layers: `mining_area` (hard filter `mining_area_active`: any part of the site excludes
it), `mining_terrain` (penalty `mining_terrain`) and `mineral_deposit` (penalty `mineral_deposit`, and it
cancels the PSI bonus). Layer 1 is "obszary górnicze" (mining areas, in force), layer 2 is "tereny górnicze"
(mining terrains) and layer 0 is "udokumentowane złoża kopalin" (documented deposits). Verified 21.10.2026
(docs/SOURCES.md): `query` answers in EPSG:2180 with `outSR=2180` and `f=geojson`, `maxRecordCount` is 1000,
and layers 1 and 2 hold 6,201 and 6,022 current features. The archive layers (3–5) are not used: an archived
area is no longer in force.

Deposits (layer 0) were left out until 11.11.2026, because a deposit is not a mining area and does not block
building by itself. The Rzepin RTB review (lignite deposit under a candidate site, 11.11.2026) showed it still
matters: building on a documented deposit needs a geological opinion and can be refused or conditioned, and it
may cost the PSI exemption. So it is a penalty and a "check" item, not a hard filter. Layer 0 has no STATUS
field (checked 11.11.2026), so the development status of a deposit (P, R, E, Z) is not available from this
service; `status` stays empty for deposits. `AKTUALIZACJA` (boundary update note, usually null) is kept as
`source_date`.

The connector asks for one bbox (a powiat), like ISOK. It pages with `resultOffset` in
`orderByFields=OBJECTID` order and checks the total against `returnCountOnly`, so a server cap cannot truncate
a layer silently. Free-text fields (`KOMENTARZ`) are not requested. An empty answer is a real empty layer: no
mining area in the powiat.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any, ClassVar

import geopandas as gpd
import pandas as pd

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.egib import BBox
from dclh_pipeline.sources.prg import POLAND_BOUNDS

MAP_URL = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/midas/MapServer"
# kind (used in our tables and constraint names) -> MapServer layer id
LAYERS: dict[str, int] = {"mining_area": 1, "mining_terrain": 2, "mineral_deposit": 0}
# Field that names the feature, per kind (the layers name it differently).
NAME_FIELDS: dict[str, str] = {
    "mining_area": "NAZWA_OG",
    "mining_terrain": "NAZWA_TG",
    "mineral_deposit": "NAZWA_ZL",
}
# Asked per layer with only the fields that layer has: since 22.10.2026 the server answers 400 "Failed to
# execute query" when outFields names a field the layer lacks (NAZWA_TG on layer 1, STATUS on layer 0).
COMMON_FIELDS = ("ID_KONTURU", "STATUS", "KOPALINA")
# Layer 0 identifies a deposit by NR_ZLOZA (one deposit has several contours) and has no STATUS.
EXTRA_FIELDS: dict[str, tuple[str, ...]] = {"mineral_deposit": ("NR_ZLOZA", "AKTUALIZACJA")}
COMMON_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {"mineral_deposit": ("KOPALINA",)}
EPSG = 2180
PAGE_SIZE = 1000
MAX_PAGES = 100
# A fixed member date, so identical answers make an identical zip and the store keeps one object.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class MidasConnector(Source):
    id: ClassVar[str] = "midas"

    def __init__(self, spec: SourceSpec, *, bbox: BBox, page_size: int = PAGE_SIZE) -> None:
        super().__init__(spec)
        x0, y0, x1, y1 = bbox
        self.bbox: BBox = (float(x0), float(y0), float(x1), float(y1))
        self.page_size = page_size

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "request.json", json.dumps({"layers": LAYERS, "bbox": self.bbox}).encode())
            for kind, layer_id in LAYERS.items():
                _write(archive, f"{kind}.geojson", self._fetch_layer(client, kind, layer_id))
        return store.save(
            self.id, buffer.getvalue(), suffix=".zip", url=MAP_URL, content_type="application/zip"
        )

    def _fetch_layer(self, client: PoliteClient, kind: str, layer_id: int) -> bytes:
        url = f"{MAP_URL}/{layer_id}/query"
        geometry = {
            "where": "1=1",
            "geometry": ",".join(str(v) for v in self.bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": EPSG,
            "spatialRel": "esriSpatialRelIntersects",
            "outSR": EPSG,
        }
        total = _json(client, url, {**geometry, "returnCountOnly": "true", "f": "json"}).get("count")
        if not isinstance(total, int):
            raise ValueError(f"MIDAS {kind}: the answer has no count; cannot check it is complete")
        features: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            page = _json(
                client,
                url,
                {
                    **geometry,
                    "outFields": ",".join(
                        (
                            *COMMON_FIELDS_BY_KIND.get(kind, COMMON_FIELDS),
                            *EXTRA_FIELDS.get(kind, ()),
                            NAME_FIELDS[kind],
                        )
                    ),
                    "returnGeometry": "true",
                    "orderByFields": "OBJECTID",
                    "resultOffset": len(features),
                    "resultRecordCount": self.page_size,
                    "f": "geojson",
                },
            )
            batch = page.get("features", [])
            features.extend(batch)
            if not batch or len(features) >= total:
                break
        else:
            raise ValueError(f"MIDAS {kind}: more than {MAX_PAGES} pages for bbox {self.bbox}")
        if len(features) < total:
            raise ValueError(
                f"MIDAS {kind}: got {len(features)} of {total} features; the response is incomplete"
            )
        return json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False).encode()

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        with zipfile.ZipFile(snapshot.path) as archive:
            request = json.loads(archive.read("request.json"))
            frames = [_read_layer(kind, archive.read(f"{kind}.geojson")) for kind in request["layers"]]
        table = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), geometry="geometry", crs=f"EPSG:{EPSG}"
        )
        _check_bounds(table)
        # Whole polygons come back; the search needs only the powiat, and a whole area is slow to overlay.
        table = table.set_geometry(table.geometry.clip_by_rect(*request["bbox"]))
        table = table[table.geometry.notna() & ~table.geometry.is_empty].reset_index(drop=True)
        # Which layers the snapshot asked for: an older snapshot has no deposits, which is not "no deposit".
        table.attrs["kinds"] = list(request["layers"])
        return table


def _json(client: PoliteClient, url: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = client.get(url, params=params)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    # ArcGIS reports most failures as HTTP 200 with an `error` object.
    if "error" in data:
        raise ValueError(f"MIDAS returned an error: {data['error']}")
    return data


def _write(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)


def _read_layer(kind: str, geojson: bytes) -> gpd.GeoDataFrame:
    features = json.loads(geojson)["features"]
    if not features:
        return _empty(kind)
    raw = gpd.GeoDataFrame.from_features(features, crs=f"EPSG:{EPSG}")
    name_field = NAME_FIELDS[kind]
    if name_field not in raw.columns:
        raise ValueError(f"MIDAS {kind}: field {name_field!r} not found; available: {sorted(raw.columns)}")
    deposit = kind == "mineral_deposit"
    id_field = "NR_ZLOZA" if deposit else "ID_KONTURU"
    missing = [c for c in (id_field, "KOPALINA") if c not in raw.columns]
    if missing:
        raise ValueError(f"MIDAS {kind}: fields {missing} not found; available: {sorted(raw.columns)}")
    empty = pd.Series("", index=raw.index)
    status = empty if deposit else raw["STATUS"]
    source_date = raw["AKTUALIZACJA"] if deposit and "AKTUALIZACJA" in raw.columns else empty
    return gpd.GeoDataFrame(
        {
            "kind": kind,
            "object_id": pd.to_numeric(raw[id_field], errors="coerce").to_numpy(),
            "status": status.fillna("").astype(str).to_numpy(),
            "name": raw[name_field].fillna("").astype(str).str.strip().to_numpy(),
            "mineral": raw["KOPALINA"].fillna("").astype(str).to_numpy(),
            "source_date": source_date.fillna("").astype(str).str.strip().to_numpy(),
        },
        geometry=raw.geometry.to_numpy(),
        crs=f"EPSG:{EPSG}",
    )


def _empty(kind: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "kind": pd.Series(dtype=str),
            "object_id": pd.Series(dtype=float),
            "status": pd.Series(dtype=str),
            "name": pd.Series(dtype=str),
            "mineral": pd.Series(dtype=str),
            "source_date": pd.Series(dtype=str),
        },
        geometry=gpd.GeoSeries([], dtype="geometry"),
        crs=f"EPSG:{EPSG}",
    )


def _check_bounds(table: gpd.GeoDataFrame) -> None:
    if table.empty:
        return
    minx, miny, maxx, maxy = table.total_bounds
    x0, y0, x1, y1 = POLAND_BOUNDS
    if minx < x0 or miny < y0 or maxx > x1 or maxy > y1:
        raise ValueError(
            f"MIDAS coordinates outside Poland ({minx:.0f},{miny:.0f} – {maxx:.0f},{maxy:.0f}); "
            "check the CRS (EPSG:2180)"
        )
