"""Site clustering: group adjacent parcels of one zone into candidate sites (brief section 5, step 4).

Parcels that touch, or sit at most `GAP_M` metres apart, within the same zone form one site.
All geometry is in EPSG:2180, so areas are m² and distances are m.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from shapely import STRtree, union_all
from shapely.geometry.base import BaseGeometry

GAP_M = 5.0  # parcels closer than this belong to the same site
EXPANSION_GAP_M = 20.0  # other parcels this close to a site count as land to grow it (about one road width)


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


def _split_by_grid(group: list[Parcel], max_site_ha: float, gap_m: float) -> list[list[Parcel]]:
    """Cut an oversized cluster along a square grid, assigning each parcel by its centroid.

    The cell area is `max_site_ha`, so each piece is roughly that size. Each cell is re-clustered, because a
    cell can hold parcels that are no longer connected to each other.
    """
    side = math.sqrt(max_site_ha * 10_000)
    cells: dict[tuple[int, int], list[Parcel]] = defaultdict(list)
    for parcel in group:
        c = parcel.geom.centroid
        cells[(math.floor(c.x / side), math.floor(c.y / side))].append(parcel)
    pieces: list[list[Parcel]] = []
    for key in sorted(cells):
        members = cells[key]
        pieces.extend([members[i] for i in comp] for comp in _components([p.geom for p in members], gap_m))
    return pieces


def _merge_small_pieces(pieces: list[list[Parcel]], min_piece_ha: float, gap_m: float) -> list[list[Parcel]]:
    """Join each piece under `min_piece_ha` to the smallest piece it touches, smallest first.

    The grid cut ignores parcel shapes, so a contiguous block can fall into two cells as two pieces that are
    each under the minimum and get dropped (Pruszków, obręb Reguły). Pieces of one cluster always touch
    another piece unless the cluster is a single piece.
    """
    pieces = [list(p) for p in pieces]

    def ha(piece: list[Parcel]) -> float:
        return float(sum(p.geom.area for p in piece)) / 10_000

    def key(i: int) -> tuple[float, list[str]]:
        return ha(pieces[i]), sorted(p.parcel_id for p in pieces[i])

    while True:
        for i in sorted((i for i in range(len(pieces)) if ha(pieces[i]) < min_piece_ha), key=key):
            geom = union_all([p.geom for p in pieces[i]])
            touching = [
                j
                for j in range(len(pieces))
                if j != i and geom.distance(union_all([p.geom for p in pieces[j]])) <= gap_m
            ]
            if touching:
                j = min(touching, key=key)
                pieces[j].extend(pieces[i])
                del pieces[i]
                break
        else:
            return pieces


def cluster_sites(
    parcels: Iterable[Parcel],
    *,
    gap_m: float = GAP_M,
    mw_per_ha: float,
    split_suggestion_ha: float,
    max_site_ha: float | None = None,
    min_piece_ha: float | None = None,
) -> list[Site]:
    """Cluster parcels into sites, per zone. Parcels with no zone are clustered among themselves.

    With `max_site_ha`, a cluster larger than that is cut into pieces of about that size
    (flag `split_cluster`). With `min_piece_ha`, a piece of a cut cluster smaller than that joins a
    neighbouring piece instead of being left too small to score.
    `mw_per_ha` and `split_suggestion_ha` come from `target` in config/scoring.yaml; a site above
    `split_suggestion_ha` gets the `needs_split_suggestion` flag.
    Without zoning, adjacency alone chains a whole town into one cluster, so the search always sets it.

    The result is sorted by `site_id` so repeated runs give the same order.
    """
    by_zone: dict[str | None, list[Parcel]] = defaultdict(list)
    for parcel in parcels:
        by_zone[parcel.zone_id].append(parcel)

    sites: list[Site] = []
    for zone_id, zone_parcels in by_zone.items():
        geoms = [p.geom for p in zone_parcels]
        groups: list[tuple[list[Parcel], bool]] = []
        for members in _components(geoms, gap_m):
            group = [zone_parcels[i] for i in members]
            if max_site_ha is not None and sum(p.geom.area for p in group) / 10_000 > max_site_ha:
                pieces = _split_by_grid(group, max_site_ha, gap_m)
                if min_piece_ha is not None:
                    pieces = _merge_small_pieces(pieces, min_piece_ha, gap_m)
                groups.extend((piece, True) for piece in pieces)
            else:
                groups.append((group, False))
        for group, was_split in groups:
            ids = tuple(sorted(p.parcel_id for p in group))
            geom = union_all([p.geom for p in group])
            area_ha = geom.area / 10_000
            flags: list[str] = []
            if len(group) > 1:
                flags.append("multi_parcel")
            if was_split:
                flags.append("split_cluster")
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
                    needs_split_suggestion=area_ha > split_suggestion_ha,
                    flags=flags,
                )
            )
    return sorted(sites, key=lambda s: s.site_id)


def expansion_ha(
    sites: Iterable[Site],
    parcel_ids: Sequence[str],
    geoms: Sequence[BaseGeometry],
    *,
    gap_m: float = EXPANSION_GAP_M,
) -> dict[str, float]:
    """Hectares of other parcels within `gap_m` of each site: neighbouring land that could be bought.

    Counts every given parcel that is not part of the site, whatever its size, owner or use: EGiB carries no
    land use here, so road and water parcels count too, and nothing says a neighbour is for sale. Parcels of
    another site count as well. Overlaps are counted once and the site itself is cut out.
    Only the given (fetched) parcels are seen, so land beyond the fetched tiles is missed.
    """
    tree = STRtree(list(geoms))
    out: dict[str, float] = {}
    for site in sites:
        own = set(site.parcel_ids)
        near = [
            geoms[i]
            for i in tree.query(site.geom, predicate="dwithin", distance=gap_m)
            if parcel_ids[i] not in own
        ]
        out[site.site_id] = float(union_all(near).difference(site.geom).area) / 10_000 if near else 0.0
    return out
