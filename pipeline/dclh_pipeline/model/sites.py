"""Site clustering: group adjacent parcels of one zone into candidate sites (brief section 5, step 4).

Parcels that touch, or sit at most `GAP_M` metres apart, within the same zone form one site.
All geometry is in EPSG:2180, so areas are m² and distances are m.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from shapely import STRtree, union_all
from shapely.geometry.base import BaseGeometry

GAP_M = 5.0  # parcels closer than this belong to the same site
SPLIT_SUGGESTION_HA = 80.0  # sites larger than this get a split suggestion (flag only, nothing is split)
DEFAULT_MW_PER_HA = 6.0  # brief: 60 MW needs about 10 ha, so about 6 MW/ha


@dataclass(frozen=True)
class Parcel:
    parcel_id: str
    zone_id: str | None
    geom: BaseGeometry


@dataclass(frozen=True)
class Site:
    site_id: str
    zone_id: str | None
    parcel_ids: tuple[str, ...]
    geom: BaseGeometry
    area_ha: float
    compactness: float  # Polsby-Popper: 4·π·A / P², 1.0 for a circle
    estimated_mw: float
    needs_split_suggestion: bool
    flags: list[str] = field(default_factory=list)


def _components(geoms: list[BaseGeometry], gap: float) -> list[list[int]]:
    """Connected components of geometries whose distance is at most `gap` (union-find over an STRtree)."""
    buffered = [g.buffer(gap / 2) for g in geoms]
    tree = STRtree(buffered)
    parent = list(range(len(geoms)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, other in enumerate(buffered):
        for j in tree.query(other, predicate="intersects"):
            if j > i:
                parent[find(i)] = find(int(j))

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(geoms)):
        groups[find(i)].append(i)
    return [sorted(members) for members in groups.values()]


def _site_id(zone_id: str | None, parcel_ids: Iterable[str]) -> str:
    """Stable ID: the same parcel set in the same zone always gives the same ID."""
    key = f"{zone_id or ''}|" + "|".join(sorted(parcel_ids))
    return "site-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _compactness(geom: BaseGeometry) -> float:
    perimeter = geom.length
    if perimeter == 0:
        return 0.0
    return float(4 * math.pi * geom.area / perimeter**2)


def cluster_sites(
    parcels: Iterable[Parcel],
    *,
    gap_m: float = GAP_M,
    mw_per_ha: float = DEFAULT_MW_PER_HA,
) -> list[Site]:
    """Cluster parcels into sites, per zone. Parcels with no zone are clustered among themselves.

    The result is sorted by `site_id` so repeated runs give the same order.
    """
    by_zone: dict[str | None, list[Parcel]] = defaultdict(list)
    for parcel in parcels:
        by_zone[parcel.zone_id].append(parcel)

    sites: list[Site] = []
    for zone_id, zone_parcels in by_zone.items():
        geoms = [p.geom for p in zone_parcels]
        for members in _components(geoms, gap_m):
            group = [zone_parcels[i] for i in members]
            ids = tuple(sorted(p.parcel_id for p in group))
            geom = union_all([p.geom for p in group])
            area_ha = geom.area / 10_000
            flags: list[str] = []
            if len(group) > 1:
                flags.append("multi_parcel")
            if zone_id is None:
                flags.append("no_zone")
            sites.append(
                Site(
                    site_id=_site_id(zone_id, ids),
                    zone_id=zone_id,
                    parcel_ids=ids,
                    geom=geom,
                    area_ha=area_ha,
                    compactness=_compactness(geom),
                    estimated_mw=area_ha * mw_per_ha,
                    needs_split_suggestion=area_ha > SPLIT_SUGGESTION_HA,
                    flags=flags,
                )
            )
    return sorted(sites, key=lambda s: s.site_id)
