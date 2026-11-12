"""RCN land transactions and the team-wide asking price per site (task 6, price comparables).

`land_transactions` keeps one row per transaction with a point (the parcels' centroid, EPSG:2180), not the
polygon. The notary, the deed number and the parties are never stored (brief 9.6). `site_asking_prices` holds
the asking price someone typed for a site; it is a team-wide figure, not personal data.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE land_transactions (
            tx_id        text PRIMARY KEY,               -- TRAN_LOKALNY_ID_IIP of the RCN register
            powiat       text NOT NULL,                  -- 4-digit TERYT, e.g. 0805
            tx_date      date NOT NULL,
            price_zl     double precision NOT NULL,
            area_ha      double precision NOT NULL,
            land_use     text NOT NULL,                  -- gruntyRolne, ..., or mieszane
            mpzp         text NOT NULL DEFAULT '',
            nier_rodzaj  text NOT NULL,
            trans_type   text NOT NULL,
            share        text NOT NULL,
            parcels      integer NOT NULL,
            geom         geometry(Point, 2180) NOT NULL,
            imported_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX land_transactions_geom_gix ON land_transactions USING gist (geom);
        CREATE INDEX land_transactions_powiat_ix ON land_transactions (powiat, tx_date);

        CREATE TABLE site_asking_prices (
            site_id     text PRIMARY KEY,
            zl_m2       double precision NOT NULL CHECK (zl_m2 > 0),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  integer REFERENCES users (id) ON DELETE SET NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_asking_prices, land_transactions")
