"""Land-price comparables from RCN transactions (pure; the API and the MCP tool call it).

Rules (docs/PLAN.md, task 6): undeveloped land, free market, share 1/1, price and area above zero;
zł/m² = price / (area_ha × 10 000); `agricultural` (`gruntyRolne`, at least `min_area_ha_agricultural`) apart
from `other` (at least `min_area_ha_other`); within `radius_km` of the site over the last `months`. Each group
is widened to the whole powiat on its own when the circle holds fewer than `min_count` of its transactions.
Thresholds come from the `rcn:` block of config/scoring.yaml. No score depends on this.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

from dclh_pipeline.config import RcnParams

SOURCE_ID = "rcn"
UNDEVELOPED = "nieruchomoscGruntowaNiezabudowana"
FREE_MARKET = "wolnyRynek"
AGRICULTURAL = "gruntyRolne"


@dataclass(frozen=True)
class Transaction:
    tx_date: date
    price_zl: float
    area_ha: float
    land_use: str
    nier_rodzaj: str
    trans_type: str
    share: str
    powiat: str
    x: float  # EPSG:2180
    y: float


@dataclass(frozen=True)
class GroupStats:
    count: int
    median_zl_m2: float | None
    p25_zl_m2: float | None
    p75_zl_m2: float | None
    first_date: date | None
    last_date: date | None
    widened: bool = False  # fewer than min_count in the circle: the figures come from the whole powiat
    confidence: str = "missing"  # verified (enough in the circle) | estimated (widened or few) | missing


@dataclass(frozen=True)
class Comparables:
    agricultural: GroupStats
    other: GroupStats
    widened: bool  # either group was widened
    radius_m: float
    months: int
    source_id: str
    as_of: datetime | None
    confidence: str  # the agricultural group's, or the other group's when there is no agricultural one


def months_ago(today: date, months: int) -> date:
    """`today` minus whole months, clamped to the month's last day (31.03 minus 1 month is 28.02)."""
    index = today.year * 12 + (today.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    for day in (today.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def price_per_m2(tx: Transaction) -> float:
    return tx.price_zl / (tx.area_ha * 10_000.0)


def is_comparable(tx: Transaction) -> bool:
    return (
        tx.nier_rodzaj == UNDEVELOPED
        and tx.trans_type == FREE_MARKET
        and tx.share == "1/1"
        and tx.price_zl > 0
        and tx.area_ha > 0
    )


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (q in 0..1) of a non-empty list."""
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _stats(items: list[Transaction], *, widened: bool, min_count: int, known: bool) -> GroupStats:
    if not items or not known:
        return GroupStats(0, None, None, None, None, None, widened=False, confidence="missing")
    prices = [price_per_m2(t) for t in items]
    dates = [t.tx_date for t in items]
    return GroupStats(
        count=len(items),
        median_zl_m2=statistics.median(prices),
        p25_zl_m2=percentile(prices, 0.25),
        p75_zl_m2=percentile(prices, 0.75),
        first_date=min(dates),
        last_date=max(dates),
        widened=widened,
        confidence="estimated" if widened or len(items) < min_count else "verified",
    )


def _group(
    usable: list[Transaction],
    belongs: Callable[[Transaction], bool],
    *,
    x: float,
    y: float,
    powiat: str | None,
    cfg: RcnParams,
    known: bool,
) -> GroupStats:
    members = [t for t in usable if belongs(t)]
    radius_m = cfg.radius_km * 1000
    near = [t for t in members if (t.x - x) ** 2 + (t.y - y) ** 2 <= radius_m**2]
    chosen, widened = near, False
    if len(near) < cfg.min_count and powiat is not None:
        in_powiat = [t for t in members if t.powiat == powiat]
        if len(in_powiat) > len(near):
            chosen, widened = in_powiat, True
    return _stats(chosen, widened=widened, min_count=cfg.min_count, known=known)


def comparables(
    transactions: Iterable[Transaction],
    *,
    x: float,
    y: float,
    powiat: str | None,
    today: date,
    as_of: datetime | None,
    cfg: RcnParams | None = None,
) -> Comparables:
    """Median, p25, p75, count and date range per land-use group around the point (x, y), EPSG:2180.

    `powiat` is the site's powiat code; a group short of transactions falls back to its transactions.
    Pass every transaction the query found; filtering is done here. Without `as_of` (nothing was ever
    imported) both groups are missing.
    """
    cfg = cfg or RcnParams()
    since = months_ago(today, cfg.months)
    usable = [t for t in transactions if is_comparable(t) and since <= t.tx_date <= today]
    known = as_of is not None

    def agricultural(t: Transaction) -> bool:
        return t.land_use == AGRICULTURAL and t.area_ha >= cfg.min_area_ha_agricultural

    def other(t: Transaction) -> bool:
        return t.land_use != AGRICULTURAL and t.area_ha >= cfg.min_area_ha_other

    agri = _group(usable, agricultural, x=x, y=y, powiat=powiat, cfg=cfg, known=known)
    rest = _group(usable, other, x=x, y=y, powiat=powiat, cfg=cfg, known=known)
    headline = agri if agri.count else rest
    return Comparables(
        agricultural=agri,
        other=rest,
        widened=agri.widened or rest.widened,
        radius_m=cfg.radius_km * 1000,
        months=cfg.months,
        source_id=SOURCE_ID,
        as_of=as_of,
        confidence=headline.confidence,
    )


def asking_price_outlier(
    asking_zl_m2: float | None, agricultural_median: float | None, factor: float = 10.0
) -> bool:
    """True when the asking price is more than `factor` times the agricultural median (needs both)."""
    if asking_zl_m2 is None or agricultural_median is None or agricultural_median <= 0:
        return False
    return asking_zl_m2 > factor * agricultural_median
