"""Geometry for the survey-drawing "sheet" of a parcel check (PLAN.md, Sprawdź działkę, architecture A5).

The server turns each layer into simple paths in local metres (EPSG:2180 minus the parcel centre, north
up), so the browser draws SVG without projection code and the scale bar is exact. Each layer is clipped to
the sheet, simplified until it fits a size cap, and left out (marked `truncated`) when it still does not fit.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from shapely import box
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

LAYER_CAP_BYTES = 60_000  # about 500 KB for a whole check at most nine layers
_TOLERANCES_M = (0.3, 1.0, 2.5, 6.0, 15.0)
Path = list[list[float]]


def sheet_bounds(parcel: BaseGeometry, margin_m: float) -> tuple[float, float, float, float]:
    """The sheet box: the parcel's bounds grown by `margin_m` on every side, in EPSG:2180."""
    x0, y0, x1, y1 = parcel.bounds
    return (x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m)


def _rings(geom: BaseGeometry, origin: tuple[float, float]) -> list[Path]:
    ox, oy = origin

    def local(coords: Iterable[tuple[float, ...]]) -> Path:
        return [[round(c[0] - ox, 1), round(c[1] - oy, 1)] for c in coords]

    if isinstance(geom, Polygon):
        return (
            [local(geom.exterior.coords), *(local(r.coords) for r in geom.interiors)]
            if not geom.is_empty
            else []
        )
    if isinstance(geom, (LineString,)):
        return [local(geom.coords)] if not geom.is_empty else []
    if isinstance(geom, Point):
        return [local(geom.coords)]
    if isinstance(geom, (MultiPolygon, MultiLineString, MultiPoint, GeometryCollection)):
        return [ring for part in geom.geoms for ring in _rings(part, origin)]
    return []


def layer_paths(
    geoms: Iterable[BaseGeometry], window: tuple[float, float, float, float], origin: tuple[float, float]
) -> tuple[list[Path], bool]:
    """Clip `geoms` to `window`: simplified local paths, `truncated` when even the coarsest is too big."""
    frame = box(*window)
    clipped = [g.intersection(frame) for g in geoms if not g.is_empty and g.intersects(frame)]
    clipped = [g for g in clipped if not g.is_empty]
    for tolerance in _TOLERANCES_M:
        paths: list[Path] = []
        for geom in clipped:
            simple = geom.simplify(tolerance, preserve_topology=True) if tolerance else geom
            paths.extend(p for p in _rings(simple, origin) if len(p) >= 1)
        if len(json.dumps(paths, separators=(",", ":"))) <= LAYER_CAP_BYTES:
            return paths, False
    return [], True


def build_sheet(
    parcel: BaseGeometry,
    layers: Sequence[tuple[str, str, str, Iterable[BaseGeometry]]],
    *,
    margin_m: float,
) -> dict[str, Any]:
    """The sheet JSON. `layers` are (key, label_pl, kind, geometries); kind: "polygon", "line" or "point"."""
    window = sheet_bounds(parcel, margin_m)
    centre = parcel.representative_point()
    origin = (round(centre.x, 1), round(centre.y, 1))
    out_layers = []
    for key, label, kind, geoms in layers:
        paths, truncated = layer_paths(list(geoms), window, origin)
        out_layers.append(
            {"key": key, "label_pl": label, "kind": kind, "paths": paths, "truncated": truncated}
        )
    x0, y0, x1, y1 = window
    return {
        "origin_2180": list(origin),
        "bbox_m": [
            round(x0 - origin[0], 1),
            round(y0 - origin[1], 1),
            round(x1 - origin[0], 1),
            round(y1 - origin[1], 1),
        ],
        "parcel": _rings(parcel, origin),
        "layers": out_layers,
    }
