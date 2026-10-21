"""Area of interest: Poland on the west/north bank of the Vistula (brief section 2.1).

The AOI is built by extending the Vistula main-stem line past the national border at both ends, splitting the
country polygon with it, and keeping the piece(s) on the western/northern side. All inputs are in EPSG:2180.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import split, unary_union

# A point that is certainly on the AOI side (Poznań) and one that is certainly not (Lublin).
KEEP_REFERENCE_LONLAT = (16.93, 52.41)
DROP_REFERENCE_LONLAT = (22.57, 51.25)

_to_2180 = Transformer.from_crs(4326, 2180, always_xy=True)


def lonlat_to_2180(lon: float, lat: float) -> Point:
    x, y = _to_2180.transform(lon, lat)
    return Point(x, y)


def extend_line(line: LineString, distance: float) -> LineString:
    """Extend both ends of `line` by `distance` metres along the direction of the end segments."""
    coords = list(line.coords)
    if len(coords) < 2:
        raise ValueError("need at least two points")

    def push(origin: tuple[float, float], toward: tuple[float, float]) -> tuple[float, float]:
        dx, dy = origin[0] - toward[0], origin[1] - toward[1]
        norm = math.hypot(dx, dy)
        if norm == 0:
            raise ValueError("degenerate end segment")
        return (origin[0] + dx / norm * distance, origin[1] + dy / norm * distance)

    start = push(coords[0][:2], coords[1][:2])
    end = push(coords[-1][:2], coords[-2][:2])
    return LineString([start, *[c[:2] for c in coords], end])


def build_aoi(
    country: Polygon | MultiPolygon,
    vistula: LineString,
    keep_point: Point,
    drop_point: Point,
    *,
    extension_m: float = 200_000,
    include: Sequence[BaseGeometry] = (),
) -> BaseGeometry:
    """Split `country` along the extended `vistula` and return the side that contains `keep_point`.

    `include` pieces (for example a city on the other bank, from `config/aoi.yaml`) are unioned in after the
    split, so they do not move the river line. Raises ValueError if the split is not clean (the reference
    points must land on different sides).
    """
    cutter = extend_line(vistula, extension_m)
    pieces = list(split(country, cutter).geoms)
    if len(pieces) < 2:
        raise ValueError("the river line does not divide the country; check the line and the boundary")
    kept = [p for p in pieces if p.contains(keep_point)]
    if not kept:
        raise ValueError("keep reference point is not inside any piece")
    # Only the piece containing the keep point is kept. If the real river line crosses itself and cuts off
    # extra western pieces, they are dropped; the area check in the Phase 1 acceptance run will show it.
    drop = [p for p in pieces if p.contains(drop_point)]
    if not drop:
        raise ValueError("drop reference point is not inside any piece")
    if any(k.equals(d) for k in kept for d in drop):
        raise ValueError("reference points fall in the same piece; the split is not usable")
    return unary_union([*kept, *include])


def simplify_for_display(aoi: BaseGeometry, tolerance_m: float = 200) -> BaseGeometry:
    return aoi.simplify(tolerance_m, preserve_topology=True)


__all__ = [
    "DROP_REFERENCE_LONLAT",
    "KEEP_REFERENCE_LONLAT",
    "build_aoi",
    "extend_line",
    "lonlat_to_2180",
    "simplify_for_display",
]
