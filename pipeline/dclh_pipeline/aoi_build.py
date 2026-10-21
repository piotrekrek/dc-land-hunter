"""Build the area of interest (brief 2.1) from the saved PRG and OSM snapshots, and load it into PostGIS.

The geometry rules live in `model/aoi.py`. This module reads the inputs: the PRG country boundary and the
Vistula main stem from the OSM extract. It writes `data/aoi/aoi.geojson` (WGS84) and replaces the `aoi`
table, which Martin serves to the map.
"""

from __future__ import annotations

import heapq
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmium
from pyproj import Transformer
from shapely import wkb
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.config import IncludedUnit
from dclh_pipeline.model.aoi import DROP_REFERENCE_LONLAT, KEEP_REFERENCE_LONLAT, build_aoi, lonlat_to_2180
from dclh_pipeline.sources.osm_power import _NodeLocations
from dclh_pipeline.sources.prg import EPSG

VISTULA_NAME = "Wisła"
_to_2180 = Transformer.from_crs(4326, EPSG, always_xy=True).transform


def _project(line: LineString) -> LineString:
    """A WGS84 line in EPSG:2180, so the main stem is measured in metres."""
    x, y = _to_2180(*np.asarray(line.coords).T)
    return LineString(np.column_stack((x, y)))


Way = tuple[int, int, LineString]  # first node id, last node id, geometry from first to last node


def main_stem(ways: Sequence[Way]) -> LineString:
    """The Vistula main stem as one line, from the ways named Wisła.

    The name matches more than the main stem: parallel channels, loops round weirs and short stubs. Each way
    is an edge between its end nodes. The stem runs between the two free ends (nodes with one way) that are
    farthest apart along the network. Loop nodes always have two ways, so they are never ends. Where a side
    channel makes a loop, the shorter branch is taken. Ways off that path are left out, so they cannot cut
    the country.
    """
    adjacency: dict[int, list[tuple[int, float, int, bool]]] = {}
    degree: Counter[int] = Counter()
    for index, (first, last, line) in enumerate(ways):
        if first == last:
            continue
        adjacency.setdefault(first, []).append((last, line.length, index, True))
        adjacency.setdefault(last, []).append((first, line.length, index, False))
        degree[first] += 1
        degree[last] += 1
    free_ends = [node for node, count in degree.items() if count == 1]
    if len(free_ends) < 2:
        raise ValueError("the Vistula ways have fewer than two free ends (a closed loop or no river at all)")

    def dijkstra(start: int) -> tuple[dict[int, float], dict[int, tuple[int, int, bool]]]:
        dist = {start: 0.0}
        prev: dict[int, tuple[int, int, bool]] = {}
        heap = [(0.0, start)]
        while heap:
            d, node = heapq.heappop(heap)
            if d > dist[node]:
                continue
            for nxt, length, index, forward in adjacency[node]:
                nd = d + length
                if nd < dist.get(nxt, math.inf):
                    dist[nxt] = nd
                    prev[nxt] = (node, index, forward)
                    heapq.heappush(heap, (nd, nxt))
        return dist, prev

    best: tuple[float, int, int] | None = None  # (length, start end, finish end)
    for start in free_ends:
        dist, _ = dijkstra(start)
        for finish in free_ends:
            if finish != start and finish in dist and (best is None or dist[finish] > best[0]):
                best = (dist[finish], start, finish)
    assert best is not None  # two distinct free ends that are connected
    _, start, finish = best
    _, prev = dijkstra(start)

    path_edges: list[tuple[int, bool]] = []  # (way index, travelled from its first node)
    node = finish
    while node != start:
        before, index, forward = prev[node]
        path_edges.append((index, forward))
        node = before
    coords: list[tuple[float, float]] = []
    for index, forward in reversed(path_edges):
        segment = list(ways[index][2].coords)
        if not forward:
            segment.reverse()
        coords.extend(segment if not coords else segment[1:])
    return LineString(coords)


def read_vistula(osm_path: Path) -> LineString:
    """The Vistula main stem (`waterway=river`, `name=Wisła`) as one line in EPSG:2180.

    Pass 1 keeps the matching ways and their node ids. Pass 2 reads the coordinates of only those nodes
    (`_NodeLocations`), so the 2 GB extract is not held in memory.
    """
    path = str(osm_path)
    found: list[np.ndarray] = []
    for obj in osmium.FileProcessor(path, osmium.osm.WAY).with_filter(osmium.filter.KeyFilter("waterway")):
        if (
            isinstance(obj, osmium.osm.Way)
            and obj.tags.get("waterway") == "river"
            and obj.tags.get("name") == VISTULA_NAME
            and len(obj.nodes) >= 2
        ):
            found.append(np.fromiter((n.ref for n in obj.nodes), dtype=np.int64))
    if not found:
        raise ValueError(f"no waterway=river named {VISTULA_NAME!r} in {osm_path.name}")
    locations = _NodeLocations.read(path, np.concatenate(found))
    ways: list[Way] = []
    for refs in found:
        geom = locations.way_geometry(refs, "line")
        if geom is None:
            raise ValueError("a Vistula node is missing from the OSM extract")
        ways.append((int(refs[0]), int(refs[-1]), _project(geom)))
    return main_stem(ways)


def country_geometry(prg: gpd.GeoDataFrame) -> Polygon | MultiPolygon:
    country = prg[prg["level"] == "country"]
    if country.empty:
        raise ValueError("PRG snapshot has no country boundary")
    return as_multipolygon(unary_union(list(country.geometry)))


def as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    """The table column is MultiPolygon; a single piece comes back from the split as a Polygon."""
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    raise ValueError(f"AOI is {geom.geom_type}, expected polygons")


def included_geometries(prg: gpd.GeoDataFrame, units: Sequence[IncludedUnit]) -> list[BaseGeometry]:
    """The PRG geometry of each unit that `config/aoi.yaml` adds to the AOI.

    A unit must match exactly one PRG row by level and TERYT code. A code that PRG does not have fails the
    build, so a typo cannot silently leave a town out.
    """
    geoms: list[BaseGeometry] = []
    for unit in units:
        match = prg[(prg["level"] == unit.level) & (prg["teryt"] == unit.teryt)]
        if len(match) != 1:
            raise ValueError(
                f"AOI include {unit.level} {unit.teryt} ({unit.name}): PRG has {len(match)} units, expected 1"
            )
        geoms.append(match.geometry.iloc[0])
    return geoms


def build(
    country: Polygon | MultiPolygon, vistula: LineString, include: Sequence[BaseGeometry] = ()
) -> MultiPolygon:
    aoi = build_aoi(
        country,
        vistula,
        lonlat_to_2180(*KEEP_REFERENCE_LONLAT),
        lonlat_to_2180(*DROP_REFERENCE_LONLAT),
        include=include,
    )
    return as_multipolygon(aoi)


def voivodeship_shares(aoi: BaseGeometry, prg: gpd.GeoDataFrame) -> list[tuple[str, float]]:
    """Share of each voivodeship's area that lies inside the AOI, largest first. The plausibility check."""
    units = prg[prg["level"] == "voivodeship"]
    shares = [
        (name, geom.intersection(aoi).area / geom.area)
        for name, geom in zip(units["name"], units.geometry, strict=True)
    ]
    return sorted(shares, key=lambda item: -item[1])


def geojson_path(data_dir: Path) -> Path:
    """Where `dclh aoi build` writes the GeoJSON: `<data dir>/aoi/aoi.geojson`.

    On the host the data dir is the repo's `data/`, so this is the tracked file. In the containers it is the
    data volume (`DCLH_DATA_DIR=/data`); the repo path there is read-only for the app user.
    """
    return data_dir / "aoi" / "aoi.geojson"


def write_geojson(aoi: BaseGeometry, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({"name": ["default"]}, geometry=[aoi], crs=f"EPSG:{EPSG}").to_crs(4326)
    gdf.to_file(path, driver="GeoJSON")


def load_aoi(session: Session, aoi: MultiPolygon, note: str) -> None:
    """Replace the AOI row. Runs inside the caller's transaction, so a failure keeps the previous AOI."""
    session.execute(text("DELETE FROM aoi"))
    session.execute(
        text(
            """
            INSERT INTO aoi (name, note, geom)
            VALUES ('default', :note, ST_SetSRID(ST_GeomFromWKB(:wkb), 2180))
            """
        ),
        {"note": note, "wkb": wkb.dumps(aoi)},
    )


__all__ = [
    "VISTULA_NAME",
    "as_multipolygon",
    "build",
    "country_geometry",
    "included_geometries",
    "load_aoi",
    "main_stem",
    "read_vistula",
    "voivodeship_shares",
    "write_geojson",
]
