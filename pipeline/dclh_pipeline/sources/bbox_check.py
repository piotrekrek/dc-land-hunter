"""The axis-order sanity check shared by the WFS connectors that answer EPSG:2180 northing-first (ISOK, RU).

A WFS is asked for the features in a bbox. If the axes were ever swapped, the answer would be a place far
from the box, and the connector must fail rather than load a river or a plan in the wrong place. But the
services match loosely: on envelopes, and for a small box (a parcel check asks for a few hundred metres)
they also return features a few hundred metres off the box. So the check is not "a feature touches the box"
but "a feature lies near it". With x and y swapped a place moves by |x - y|, tens to hundreds of kilometres
almost everywhere in Poland, far beyond this tolerance.
"""

from __future__ import annotations

import geopandas as gpd
from shapely import box

NEAR_M = 2_000.0


def features_near_bbox(table: gpd.GeoDataFrame, bbox: tuple[float, float, float, float]) -> bool:
    """True when the table is empty, or at least one feature's extent is within `NEAR_M` of `bbox`."""
    if table.empty:
        return True
    return bool((table.geometry.envelope.distance(box(*bbox)) <= NEAR_M).any())
