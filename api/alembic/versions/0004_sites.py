"""Scored candidate sites (brief section 5, step 4 and section 6).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sites (
            site_id          text PRIMARY KEY,           -- stable: hash of zone and parcel ids
            zone_id          text,
            parcel_ids       text[] NOT NULL,
            area_ha          double precision NOT NULL,
            compactness      double precision NOT NULL,
            estimated_mw     double precision NOT NULL,
            score            double precision NOT NULL,
            upside_score     double precision NOT NULL,
            completeness     double precision NOT NULL CHECK (completeness BETWEEN 0 AND 1),
            excluded         boolean NOT NULL,
            grid_badge       text NOT NULL,
            large_campus     boolean NOT NULL,
            split_suggested  boolean NOT NULL,
            flags            text[] NOT NULL DEFAULT '{}',
            detail           jsonb NOT NULL,             -- score breakdown and features, with provenance
            scored_at        timestamptz NOT NULL,
            geom             geometry(Geometry, 2180) NOT NULL
        );
        CREATE INDEX sites_geom_gix ON sites USING gist (geom);
        CREATE INDEX sites_score_ix ON sites (score DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sites")
