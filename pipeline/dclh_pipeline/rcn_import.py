"""Import RCN land transactions of one powiat into `land_transactions` (CLI `dclh import-rcn` and the job).

Upsert by transaction id, so a repeated import refreshes rows and never duplicates them. Apartments are
skipped (they are not land). The powiat's bounding box comes from `admin_units` (EPSG:2180).
"""

from __future__ import annotations

import logging
from typing import Any

import geopandas as gpd
from sqlalchemy import text
from sqlalchemy.orm import Session

from dclh_pipeline.config import load_sources
from dclh_pipeline.http import PoliteClient
from dclh_pipeline.settings import get_settings
from dclh_pipeline.snapshots import SnapshotStore
from dclh_pipeline.sources.rcn import RcnConnector

log = logging.getLogger(__name__)

_UPSERT = text(
    """
    INSERT INTO land_transactions (tx_id, powiat, tx_date, price_zl, area_ha, land_use, mpzp, nier_rodzaj,
                                   trans_type, share, parcels, geom, imported_at)
    VALUES (:tx_id, :powiat, :tx_date, :price_zl, :area_ha, :land_use, :mpzp, :nier_rodzaj,
            :trans_type, :share, :parcels, ST_SetSRID(ST_MakePoint(:x, :y), 2180), now())
    ON CONFLICT (tx_id) DO UPDATE SET
        powiat = EXCLUDED.powiat, tx_date = EXCLUDED.tx_date, price_zl = EXCLUDED.price_zl,
        area_ha = EXCLUDED.area_ha, land_use = EXCLUDED.land_use, mpzp = EXCLUDED.mpzp,
        nier_rodzaj = EXCLUDED.nier_rodzaj, trans_type = EXCLUDED.trans_type, share = EXCLUDED.share,
        parcels = EXCLUDED.parcels, geom = EXCLUDED.geom, imported_at = EXCLUDED.imported_at
    """
)
_BBOX = text(
    """
    SELECT ST_XMin(geom) AS x0, ST_YMin(geom) AS y0, ST_XMax(geom) AS x1, ST_YMax(geom) AS y1
    FROM admin_units WHERE level = 'powiat' AND teryt = :teryt
    """
)


def powiat_bbox(session: Session, powiat: str) -> tuple[float, float, float, float]:
    row = session.execute(_BBOX, {"teryt": powiat.zfill(4)}).mappings().first()
    if row is None:
        raise ValueError(f"no powiat {powiat!r} in admin_units; run `dclh load-admin` first")
    return (float(row["x0"]), float(row["y0"]), float(row["x1"]), float(row["y1"]))


def land_rows(table: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    """Rows to upsert: land only, with the point split into x and y."""
    land = table[table["nier_rodzaj"].str.startswith("nieruchomoscGruntowa")]
    rows: list[dict[str, Any]] = []
    for rec in land.itertuples(index=False):
        rows.append(
            {
                "tx_id": rec.tx_id,
                "powiat": rec.powiat,
                "tx_date": rec.tx_date,
                "price_zl": rec.price_zl,
                "area_ha": rec.area_ha,
                "land_use": rec.land_use,
                "mpzp": rec.mpzp,
                "nier_rodzaj": rec.nier_rodzaj,
                "trans_type": rec.trans_type,
                "share": rec.share,
                "parcels": int(rec.parcels),
                "x": rec.geometry.x,
                "y": rec.geometry.y,
            }
        )
    return rows


def import_powiat(session: Session, powiat: str) -> dict[str, Any]:
    """Download and upsert one powiat. Returns a summary for `runs.summary` and the CLI."""
    settings = get_settings()
    if settings.contact_email.endswith(".invalid"):
        raise ValueError("set DCLH_CONTACT_EMAIL first (it goes into the User-Agent)")
    config = load_sources()
    bbox = powiat_bbox(session, powiat)
    connector = RcnConnector(config.get("rcn"), powiat=powiat, bbox=bbox)
    store = SnapshotStore(settings.raw_dir)
    with PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_per_s=config.defaults.rate_limit_per_s,
        timeout_s=config.defaults.timeout_s,
        retries=2,
    ) as client:
        snapshot = connector.fetch(client, store)
    table = connector.parse(snapshot)
    store.mark_good(snapshot)
    rows = land_rows(table)
    if rows:
        session.execute(_UPSERT, rows)
    return {"powiat": connector.powiat, "transactions": len(table), "land_rows": len(rows)}
