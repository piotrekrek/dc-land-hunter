"""Land-price comparables of a site: RCN transactions around it plus the team's asking price.

Shared by `GET /sites/{id}/prices` and the MCP `get_site` card. The statistics come from the pure
`dclh_pipeline.comparables`; this module only loads the rows. No score depends on it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_api.schemas import PriceComparables, PriceGroup, SitePrices
from dclh_pipeline import comparables as cmp

_SITE = text(
    """
    SELECT ST_X(p) AS x, ST_Y(p) AS y, powiat
    FROM (SELECT ST_PointOnSurface(geom) AS p, powiat FROM sites WHERE site_id = :s) t
    """
)
# Only rows that can be comparable; the pure function applies the same rules again (cheap, and one place).
_TRANSACTIONS = text(
    """
    SELECT tx_date, price_zl, area_ha, land_use, nier_rodzaj, trans_type, share, powiat,
           ST_X(geom) AS x, ST_Y(geom) AS y
    FROM land_transactions
    WHERE tx_date BETWEEN :since AND :today
      AND nier_rodzaj = :nier AND trans_type = :trans AND share = '1/1'
      AND price_zl > 0 AND area_ha > 0
      AND (powiat = :powiat OR ST_DWithin(geom, ST_SetSRID(ST_MakePoint(:x, :y), 2180), :radius))
    """
)
_AS_OF = text("SELECT max(imported_at) FROM land_transactions WHERE powiat = :powiat")
_ASKING = text("SELECT zl_m2, updated_at FROM site_asking_prices WHERE site_id = :s")


def _group(stats: cmp.GroupStats) -> PriceGroup:
    return PriceGroup(
        count=stats.count,
        median_zl_m2=stats.median_zl_m2,
        p25_zl_m2=stats.p25_zl_m2,
        p75_zl_m2=stats.p75_zl_m2,
        first_date=stats.first_date,
        last_date=stats.last_date,
    )


def site_prices(session: Session, site_id: str, today: date | None = None) -> SitePrices | None:
    """Comparables and asking price of a site, or None when there is no such site."""
    site = session.execute(_SITE, {"s": site_id}).mappings().first()
    if site is None:
        return None
    today = today or datetime.now(UTC).date()
    rows = (
        session.execute(
            _TRANSACTIONS,
            {
                "since": cmp.months_ago(today, cmp.MONTHS),
                "today": today,
                "nier": cmp.UNDEVELOPED,
                "trans": cmp.FREE_MARKET,
                "powiat": site["powiat"],
                "x": site["x"],
                "y": site["y"],
                "radius": cmp.RADIUS_M,
            },
        )
        .mappings()
        .all()
    )
    as_of = session.execute(_AS_OF, {"powiat": site["powiat"]}).scalar_one()
    result = cmp.comparables(
        (_transaction(r) for r in rows),
        x=float(site["x"]),
        y=float(site["y"]),
        powiat=site["powiat"],
        today=today,
        as_of=as_of,
    )
    asking = session.execute(_ASKING, {"s": site_id}).mappings().first()
    asking_zl = float(asking["zl_m2"]) if asking else None
    return SitePrices(
        comparables=PriceComparables(
            agricultural=_group(result.agricultural),
            other=_group(result.other),
            widened=result.widened,
            radius_km=result.radius_m / 1000,
            months=result.months,
            source_id=result.source_id,
            as_of=result.as_of,
            confidence=result.confidence,
        ),
        asking_zl_m2=asking_zl,
        asking_updated_at=asking["updated_at"] if asking else None,
        asking_outlier=cmp.asking_price_outlier(asking_zl, result.agricultural.median_zl_m2),
    )


def _transaction(row: Any) -> cmp.Transaction:
    return cmp.Transaction(
        tx_date=row["tx_date"],
        price_zl=float(row["price_zl"]),
        area_ha=float(row["area_ha"]),
        land_use=row["land_use"],
        nier_rodzaj=row["nier_rodzaj"],
        trans_type=row["trans_type"],
        share=row["share"],
        powiat=row["powiat"],
        x=float(row["x"]),
        y=float(row["y"]),
    )


def compact(prices: SitePrices) -> dict[str, Any]:
    """The short form the MCP card carries: medians and counts, no dates per group."""
    comp = prices.comparables

    def line(group: PriceGroup) -> dict[str, Any]:
        return {
            "count": group.count,
            "median_zl_m2": _round(group.median_zl_m2),
            "p25_zl_m2": _round(group.p25_zl_m2),
            "p75_zl_m2": _round(group.p75_zl_m2),
        }

    return {
        "agricultural": line(comp.agricultural),
        "other": line(comp.other),
        "widened_to_powiat": comp.widened,
        "radius_km": comp.radius_km,
        "months": comp.months,
        "asking_zl_m2": prices.asking_zl_m2,
        "asking_outlier": prices.asking_outlier,
        "source_id": comp.source_id,
        "as_of": comp.as_of.date().isoformat() if comp.as_of else None,
        "confidence": comp.confidence,
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)
