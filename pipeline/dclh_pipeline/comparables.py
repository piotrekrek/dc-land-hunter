"""Land-price comparables from RCN transactions (pure; the API and the MCP tool call it).

Rules (docs/PLAN.md, task 6): undeveloped land, free market, share 1/1, price and area above zero;
zł/m² = price / (area_ha × 10 000); `agricultural` (`gruntyRolne`) apart from `other`; within 10 km of the
site over the last 36 months. Fewer than 5 transactions in the circle (both groups together) widen the
search to the whole powiat, and the result says so. No score depends on this.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

SOURCE_ID = "rcn"
RADIUS_M = 10_000.0
MONTHS = 36
MIN_COUNT = 5
OUTLIER_FACTOR = 10.0
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


@dataclass(frozen=True)
class Comparables:
    agricultural: GroupStats
    other: GroupStats
    widened: bool
    radius_m: float
    months: int
    source_id: str
    as_of: datetime | None
    confidence: str  # verified | estimated | missing


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


def _stats(items: list[Transaction]) -> GroupStats:
    if not items:
        return GroupStats(0, None, None, None, None, None)
    prices = [price_per_m2(t) for t in items]
    dates = [t.tx_date for t in items]
    return GroupStats(
        count=len(items),
        median_zl_m2=statistics.median(prices),
        p25_zl_m2=percentile(prices, 0.25),
        p75_zl_m2=percentile(prices, 0.75),
        first_date=min(dates),
        last_date=max(dates),
    )


def comparables(
    transactions: Iterable[Transaction],
    *,
    x: float,
    y: float,
    powiat: str | None,
    today: date,
    as_of: datetime | None,
    radius_m: float = RADIUS_M,
    months: int = MONTHS,
    min_count: int = MIN_COUNT,
) -> Comparables:
    """Median, p25, p75, count and date range per land-use group around the point (x, y), EPSG:2180.

    `powiat` is the site's powiat code; the fallback uses its transactions. Pass every transaction the
    query found; filtering is done here.
    """
    since = months_ago(today, months)
    usable = [t for t in transactions if is_comparable(t) and since <= t.tx_date <= today]
    near = [t for t in usable if (t.x - x) ** 2 + (t.y - y) ** 2 <= radius_m**2]
    widened = False
    chosen = near
    if len(near) < min_count and powiat is not None:
        in_powiat = [t for t in usable if t.powiat == powiat]
        if len(in_powiat) > len(near):
            chosen, widened = in_powiat, True
    agricultural = [t for t in chosen if t.land_use == AGRICULTURAL]
    other = [t for t in chosen if t.land_use != AGRICULTURAL]
    if not chosen or as_of is None:
        confidence = "missing"
    elif widened or len(chosen) < min_count:
        confidence = "estimated"
    else:
        confidence = "verified"
    return Comparables(
        agricultural=_stats(agricultural),
        other=_stats(other),
        widened=widened,
        radius_m=radius_m,
        months=months,
        source_id=SOURCE_ID,
        as_of=as_of,
        confidence=confidence,
    )


def asking_price_outlier(asking_zl_m2: float | None, agricultural_median: float | None) -> bool:
    """True when the asking price is more than 10 times the agricultural median (needs both)."""
    if asking_zl_m2 is None or agricultural_median is None or agricultural_median <= 0:
        return False
    return asking_zl_m2 > OUTLIER_FACTOR * agricultural_median
