"""OpenStreetMap power grid from the Geofabrik Poland extract (brief section 5, step 2; plan Phase 1).

`fetch()` streams `poland-latest.osm.pbf` to disk (about 2 GB, never held in memory) and checks the size
against `Content-Length`, so a cut-off download cannot become a snapshot. `parse()` reads substations
(`power=substation`, nodes and ways) and lines (`power=line` ways) with pyosmium and returns one table in
EPSG:2180 with the columns `kind`, `osm_id`, `name`, `operator`, `voltage` (the raw tag) and `geometry`.

Known gaps, on purpose:
- Substations mapped as multipolygon relations are skipped. They are rare.
- Lines are `power=line` only. `minor_line` is distribution below 110 kV, which the scorer does not use.
- Generators (`power=generator`, renewable clusters) are not read yet.

`to_grid()` turns the table into the `Substation` and `Line` objects the scorer's grid index uses. A
substation's `name` is what lets DSO capacity lists be matched to it (`model/gpz_match.py`).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import osmium
import pandas as pd
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import Line, Substation
from dclh_pipeline.model.grid import WAY_ID_OFFSET, classify_substation, parse_voltages
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.prg import EPSG, POLAND_BOUNDS

PBF_URL = "https://download.geofabrik.de/europe/poland-latest.osm.pbf"
COLUMNS = ["kind", "osm_id", "name", "operator", "voltage"]
# A real extract has thousands of substations. Far fewer means a wrong or empty file.
MIN_SUBSTATIONS_IN_EXTRACT = 1000


class OsmPowerConnector(Source):
    id: ClassVar[str] = "osm_power"

    def __init__(
        self, spec: SourceSpec, *, url: str = PBF_URL, min_substations: int = MIN_SUBSTATIONS_IN_EXTRACT
    ) -> None:
        super().__init__(spec)
        self.url = url
        self.min_substations = min_substations

    def fetch(self, client: PoliteClient, store: SnapshotStore) -> Snapshot:
        tmp_root = store.root / self.id / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
            dest = Path(tmp) / "poland.osm.pbf"
            headers = client.download(self.url, dest)
            expected = headers.get("content-length")
            if expected is not None and dest.stat().st_size != int(expected):
                raise ValueError(f"OSM download is {dest.stat().st_size} bytes, server announced {expected}")
            return store.save_file(
                self.id,
                dest,
                suffix=".osm.pbf",
                url=self.url,
                content_type=headers.get("content-type"),
            )

    def parse(self, snapshot: Snapshot) -> gpd.GeoDataFrame:
        rows: list[dict[str, object]] = []
        geoms: list[BaseGeometry] = []
        processor = (
            osmium.FileProcessor(str(snapshot.path), osmium.osm.NODE | osmium.osm.WAY)
            .with_locations()
            .with_filter(osmium.filter.KeyFilter("power"))
        )
        for obj in processor:
            if not isinstance(obj, (osmium.osm.Node, osmium.osm.Way)):
                continue
            is_way = isinstance(obj, osmium.osm.Way)
            kind = _kind(obj.tags.get("power"), is_way=is_way)
            if kind is None:
                continue
            geom = _way_geometry(obj, kind) if isinstance(obj, osmium.osm.Way) else _node_point(obj)
            if geom is None:
                continue
            rows.append(
                {
                    "kind": kind,
                    "osm_id": obj.id + WAY_ID_OFFSET if is_way else obj.id,
                    "name": obj.tags.get("name", ""),
                    "operator": obj.tags.get("operator", ""),
                    "voltage": obj.tags.get("voltage", ""),
                }
            )
            geoms.append(geom)
        table = gpd.GeoDataFrame(pd.DataFrame(rows, columns=COLUMNS), geometry=geoms, crs="EPSG:4326")
        table = table.to_crs(f"EPSG:{EPSG}")
        _check(table, self.min_substations)
        return table


def _kind(power: str | None, *, is_way: bool) -> str | None:
    if power in ("substation", "sub_station"):
        return "substation"
    if power == "line" and is_way:
        return "line"
    return None


def _node_point(node: osmium.osm.Node) -> BaseGeometry | None:
    if not node.location.valid():
        return None
    return Point(node.location.lon, node.location.lat)


def _way_geometry(way: osmium.osm.Way, kind: str) -> BaseGeometry | None:
    try:
        coords = [(n.lon, n.lat) for n in way.nodes]
    except osmium.InvalidLocationError:
        return None  # a member node is missing from the extract (cut at the border)
    if len(coords) < 2:
        return None
    if kind == "line":
        return LineString(coords)
    if len(coords) >= 4 and coords[0] == coords[-1]:
        return Polygon(coords).centroid  # a substation outline: use its centre as the location
    return LineString(coords).centroid


def _check(table: gpd.GeoDataFrame, min_substations: int) -> None:
    substations = int((table["kind"] == "substation").sum())
    if substations < min_substations:
        raise ValueError(
            f"OSM extract has {substations} substations, expected at least {min_substations}; "
            "wrong or empty file?"
        )
    x0, y0, x1, y1 = POLAND_BOUNDS
    minx, miny, maxx, maxy = table[table["kind"] == "substation"].total_bounds
    # The extract may reach a little past the border, so the box is widened by 50 km first.
    margin = 50_000.0
    if minx < x0 - margin or miny < y0 - margin or maxx > x1 + margin or maxy > y1 + margin:
        raise ValueError(
            f"OSM substations outside Poland ({minx:.0f},{miny:.0f} – {maxx:.0f},{maxy:.0f}); wrong extract?"
        )


def to_grid(table: gpd.GeoDataFrame) -> tuple[list[Substation], list[Line]]:
    """Classify the substations and read the line voltages. Unnamed stations keep `name=None`."""
    substations: list[Substation] = []
    lines: list[Line] = []
    for rec in table.itertuples(index=False):
        voltage_tag = str(rec.voltage) or None
        if rec.kind == "substation":
            info = classify_substation(voltage_tag)
            substations.append(
                Substation(
                    int(rec.osm_id),
                    info.cls,
                    info.voltages,
                    rec.geometry,
                    name=str(rec.name) or None,
                    operator=str(rec.operator) or None,
                )
            )
        else:
            volts = parse_voltages(voltage_tag)
            lines.append(Line(int(rec.osm_id), volts[0] if volts else None, rec.geometry))
    return substations, lines
