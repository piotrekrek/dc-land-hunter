"""OpenStreetMap power grid from the Geofabrik Poland extract (brief section 5, step 2; plan Phase 1).

`fetch()` streams `poland-latest.osm.pbf` to disk (about 2 GB, never held in memory) and checks the size
against `Content-Length`, so a cut-off download cannot become a snapshot. `parse()` reads substations
(`power=substation`, nodes and ways) and lines (`power=line` ways) with pyosmium and returns one table in
EPSG:2180 with the columns `kind`, `osm_id`, `name`, `operator`, `voltage` (the raw tag) and `geometry`.

Known gaps, on purpose:
- Substations mapped as multipolygon relations are skipped. They are rare.
- Lines are `power=line` only. `minor_line` is distribution below 110 kV, which the scorer does not use.
- Generators (`power=generator`, wind and solar clusters) are read by `osm_res.py` from the same extract.

`to_grid()` turns the table into the `Substation` and `Line` objects the scorer's grid index uses. A
substation's `name` is what lets DSO capacity lists be matched to it (`model/gpz_match.py`).
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import shutil
import subprocess
import tempfile
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import geopandas as gpd
import numpy as np
import osmium
import pandas as pd
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from dclh_pipeline.config import SourceSpec
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.model.features import Line, Substation
from dclh_pipeline.model.grid import (
    V_110,
    V_220,
    WAY_ID_OFFSET,
    SubstationClass,
    classify_substation,
    derive_operator,
    parse_voltages,
)
from dclh_pipeline.snapshots import Snapshot, SnapshotStore
from dclh_pipeline.sources.base import Source
from dclh_pipeline.sources.prg import EPSG, POLAND_BOUNDS

PBF_URL = "https://download.geofabrik.de/europe/poland-latest.osm.pbf"
log = logging.getLogger(__name__)

COLUMNS = ["kind", "osm_id", "name", "operator", "voltage"]
# A real extract has thousands of substations. Far fewer means a wrong or empty file.
MIN_SUBSTATIONS_IN_EXTRACT = 1000
# `parse()` peaks at a few hundred MB. It refuses to start when less than this is available, so a parse on a
# busy 2 GB VPS fails with a clear message instead of being killed by the out-of-memory killer (which also
# hits the database and the API running on the same host).
MIN_AVAILABLE_MB = 800
# `osmium.filter.IdFilter` keeps a dense bitmap over the node-id range it covers (about 4 MB per 33 million
# ids). One filter over all of Poland's power nodes (ids up to about 13 billion) needed 1.3 GB, so the node
# pass runs in batches whose ids span at most this much: about 256 MB each, at the price of one extra read of
# the file per batch. Measured on the VPS: peak 1,689 MB with one filter, 470 MB with batches.
MAX_ID_SPAN = 2**31


class OsmPowerConnector(Source):
    id: ClassVar[str] = "osm_power"

    def __init__(
        self,
        spec: SourceSpec,
        *,
        url: str = PBF_URL,
        min_substations: int = MIN_SUBSTATIONS_IN_EXTRACT,
        min_available_mb: int = MIN_AVAILABLE_MB,
    ) -> None:
        super().__init__(spec)
        self.url = url
        self.min_substations = min_substations
        self.min_available_mb = min_available_mb

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
        """Read the power objects in two passes, so memory stays small.

        One pass with a location index for every node in Poland (over 200 million) needs several GB and was
        killed by the out-of-memory killer on a 2 GB VPS. Here only the nodes that power ways use are kept
        (a few million), plus the power-tagged nodes.
        """
        require_free_memory(self.min_available_mb)
        path = str(_power_source(snapshot.path))
        started = time.monotonic()
        ways, power_nodes = _power_objects(path)
        log.info(
            "OSM power: read %d ways and %d substation nodes in %.0f s",
            len(ways),
            len(power_nodes),
            time.monotonic() - started,
        )
        started = time.monotonic()
        locations = _NodeLocations.read(path, np.concatenate([way.refs for way in ways]) if ways else None)
        log.info(
            "OSM power: read %d node locations in %.0f s", len(locations.ids), time.monotonic() - started
        )
        rows: list[dict[str, object]] = []
        geoms: list[BaseGeometry] = []
        for way in ways:
            geom = locations.way_geometry(way.refs, way.kind)
            if geom is not None:  # None: a member node is missing from the extract (cut at the border)
                rows.append(way.row())
                geoms.append(geom)
        for node_row, point in power_nodes:
            rows.append(node_row)
            geoms.append(point)
        table = gpd.GeoDataFrame(pd.DataFrame(rows, columns=COLUMNS), geometry=geoms, crs="EPSG:4326")
        table = table.to_crs(f"EPSG:{EPSG}")
        _check(table, self.min_substations)
        return table


def available_memory_mb() -> int | None:
    """`MemAvailable` from /proc/meminfo in MB, or None where it is not readable (not Linux).

    Swap is deliberately not counted: a parse that has to live in swap is a parse that stalls the whole host.
    """
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def require_free_memory(min_mb: int) -> None:
    free = available_memory_mb()
    if free is not None and free < min_mb:
        raise MemoryError(
            f"only {free} MB of memory is available, the OSM parse wants at least {min_mb} MB; "
            "stop other jobs (or the heavy containers) and retry"
        )


# What the parser needs: substation and power-line ways, substation nodes. The nodes that these ways reference
# are kept by osmium by default, so the line geometry still works.
POWER_FILTER = (
    "w/power=line",
    "w/power=substation",
    "w/power=sub_station",
    "n/power=substation",
    "n/power=sub_station",
)


def _filter_key(filters: tuple[str, ...] | None = None) -> str:
    """A short hash of the filter expressions, so a changed filter never reuses an old cut."""
    return hashlib.sha1("|".join(POWER_FILTER if filters is None else filters).encode()).hexdigest()[:8]


def prefilter_power(source: Path, target: Path, filters: tuple[str, ...] | None = None) -> Path:
    """Cut the extract down to the objects `filters` selects (default: power) with osmium-tool, then rename it
    into place. Other readers of the same extract (`osm_infra.py`) pass their own filters."""
    filters = POWER_FILTER if filters is None else filters
    tmp = target.with_name(target.name + ".tmp")
    command = [
        "osmium",
        "tags-filter",
        "--overwrite",
        "--output-format",
        "pbf",
        "--output",
        str(tmp),
        str(source),
    ]
    started = time.monotonic()
    try:
        subprocess.run([*command, *filters], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"osmium tags-filter failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    tmp.replace(target)
    log.info("OSM power: osmium cut %s to %s in %.0f s", source.name, target.name, time.monotonic() - started)
    return target


def _power_source(snapshot_path: Path, filters: tuple[str, ...] | None = None, label: str = "power") -> Path:
    """The file to parse: a cached cut (default: power only) when osmium-tool is installed, otherwise the full
    extract.

    The cut gives the same result as the full file; it saves some reading, not memory. The cache name carries
    the label and the filter's hash, so changing the filter builds a new cut.
    """
    if shutil.which("osmium") is None:
        log.info("OSM %s: osmium-tool not installed, reading the full extract", label)
        return snapshot_path
    cut = snapshot_path.with_name(
        snapshot_path.name.removesuffix(".osm.pbf") + f".{label}-{_filter_key(filters)}.osm.pbf"
    )
    if cut.exists():
        log.info("OSM %s: using the cached %s-only cut %s", label, label, cut.name)
    else:
        prefilter_power(snapshot_path, cut, filters)
    return cut


@dataclass(frozen=True)
class _PowerWay:
    osm_id: int
    kind: str
    name: str
    operator: str
    voltage: str
    refs: np.ndarray  # node ids, in order

    def row(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "osm_id": self.osm_id,
            "name": self.name,
            "operator": self.operator,
            "voltage": self.voltage,
        }


def _kind(power: str | None, *, is_way: bool) -> str | None:
    if power in ("substation", "sub_station"):
        return "substation"
    if power == "line" and is_way:
        return "line"
    return None


def _power_objects(path: str) -> tuple[list[_PowerWay], list[tuple[dict[str, object], BaseGeometry]]]:
    """Pass 1: power ways (outlines, lines) with their node ids, and single-node substations."""
    ways: list[_PowerWay] = []
    nodes: list[tuple[dict[str, object], BaseGeometry]] = []
    entities = osmium.osm.WAY | osmium.osm.NODE
    for obj in osmium.FileProcessor(path, entities).with_filter(osmium.filter.KeyFilter("power")):
        if isinstance(obj, osmium.osm.Way):
            kind = _kind(obj.tags.get("power"), is_way=True)
            refs = np.fromiter((n.ref for n in obj.nodes), dtype=np.int64)
            if kind is None or len(refs) < 2:
                continue
            ways.append(
                _PowerWay(
                    osm_id=obj.id + WAY_ID_OFFSET,
                    kind=kind,
                    name=obj.tags.get("name") or "",
                    operator=obj.tags.get("operator") or "",
                    voltage=obj.tags.get("voltage") or "",
                    refs=refs,
                )
            )
        elif isinstance(obj, osmium.osm.Node) and obj.location.valid():
            kind = _kind(obj.tags.get("power"), is_way=False)
            if kind is None:
                continue
            row: dict[str, object] = {
                "kind": kind,
                "osm_id": obj.id,
                "name": obj.tags.get("name", ""),
                "operator": obj.tags.get("operator", ""),
                "voltage": obj.tags.get("voltage", ""),
            }
            nodes.append((row, Point(obj.location.lon, obj.location.lat)))
    return ways, nodes


def _id_batches(wanted: np.ndarray | None, max_span: int = MAX_ID_SPAN) -> list[list[int]]:
    """Sorted unique ids split into runs whose first and last id are less than `max_span` apart."""
    if wanted is None or not len(wanted):
        return []
    unique = np.unique(wanted.astype(np.int64))
    starts = [0]
    while True:
        nxt = int(np.searchsorted(unique, unique[starts[-1]] + max_span, side="left"))
        if nxt >= len(unique):
            break
        starts.append(nxt)
    bounds = [*starts, len(unique)]
    return [unique[a:b].tolist() for a, b in itertools.pairwise(bounds)]


class _NodeLocations:
    """Longitude and latitude of a chosen set of nodes, sorted by id for lookup."""

    def __init__(self, ids: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> None:
        order = np.argsort(ids)
        self.ids, self.lon, self.lat = ids[order], lon[order], lat[order]

    @classmethod
    def read(cls, path: str, wanted: np.ndarray | None, max_span: int = MAX_ID_SPAN) -> _NodeLocations:
        """Pass 2: only the nodes in `wanted` are decoded; the other 200 million are skipped.

        The ids go through osmium's `IdFilter` in batches that each span less than `max_span`, because one
        filter over the whole id range allocates over a gigabyte (see `MAX_ID_SPAN`).
        """
        ids: array[int] = array("q")
        lon: array[float] = array("d")
        lat: array[float] = array("d")
        for batch in _id_batches(wanted, max_span):
            for obj in osmium.FileProcessor(path, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(batch)):
                if isinstance(obj, osmium.osm.Node) and obj.location.valid():
                    ids.append(obj.id)
                    lon.append(obj.location.lon)
                    lat.append(obj.location.lat)
        return cls(
            np.frombuffer(ids, dtype=np.int64),
            np.frombuffer(lon, dtype=np.float64),
            np.frombuffer(lat, dtype=np.float64),
        )

    def way_geometry(self, refs: np.ndarray, kind: str) -> BaseGeometry | None:
        if not len(self.ids):
            return None
        pos = np.searchsorted(self.ids, refs)
        if (pos >= len(self.ids)).any() or (self.ids[pos] != refs).any():
            return None  # a member node is not in the extract
        coords = np.column_stack((self.lon[pos], self.lat[pos]))
        if kind == "line":
            return LineString(coords)
        if len(coords) >= 4 and (coords[0] == coords[-1]).all():
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


def summarize_grid(table: gpd.GeoDataFrame) -> list[tuple[str, int]]:
    """Counts that show whether the extract is usable for matching, as (label, count) rows.

    Most `power=substation` objects in OSM are small medium-voltage transformer stations. What matters here
    is how many are 110 kV GPZs, how many of those have a name (needed to match DSO capacity lists) and an
    operator (needed to tell a factory's own substation from a utility one), and which voltage the lines
    carry.
    """
    substations, lines = to_grid(table)
    rows: list[tuple[str, int]] = [("substations, all", len(substations))]
    for cls in SubstationClass:
        rows.append((f"  class {cls.value}", sum(1 for s in substations if s.cls is cls)))
    gpz = [s for s in substations if s.cls is SubstationClass.GPZ_110_SN]
    rows.append(("GPZ 110 kV, with a name", sum(1 for s in gpz if s.name)))
    rows.append(("GPZ 110 kV, with an operator tag", sum(1 for s in gpz if s.operator)))
    for dso in ("enea", "energa", "tauron", "pge", "stoen", "pse"):
        rows.append(
            (
                f"  operator is {dso}",
                sum(1 for s in gpz if (d := derive_operator(s.operator)) and d.value == dso),
            )
        )
    rows.append(
        (
            "  operator is another named owner",
            sum(1 for s in gpz if s.operator and derive_operator(s.operator) is None),
        )
    )
    rows.append(
        (
            "110 kV substations with an owner that is not a DSO or PSE",
            sum(
                1
                for s in substations
                if any(V_110 <= v < V_220 for v in s.voltages)
                and s.operator
                and derive_operator(s.operator) is None
            ),
        )
    )
    rows.append(("lines, all", len(lines)))
    bands: list[tuple[str, Callable[[int | None], bool]]] = [
        ("  voltage 110 kV band", lambda v: v is not None and V_110 <= v < V_220),
        ("  voltage 220 kV and above", lambda v: v is not None and v >= V_220),
        ("  voltage below 110 kV", lambda v: v is not None and v < V_110),
        ("  voltage missing", lambda v: v is None),
    ]
    for label, test in bands:
        rows.append((label, sum(1 for ln in lines if test(ln.voltage))))
    return rows
