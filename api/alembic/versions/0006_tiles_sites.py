"""Candidate sites as a Martin map layer (PLAN.md, Phase 1.5; brief 8.1: the map shows the shortlist).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only the properties the map styles and the click handler need. Geometry keeps its SRID (2180); Martin
    # reprojects it to Web Mercator for the tiles, as for the other map views (migration 0002).
    op.execute(
        """
        CREATE VIEW tiles.sites AS
            SELECT site_id, powiat, score, excluded, area_ha, estimated_mw,
                   geom::geometry(Geometry, 2180) AS geom
            FROM sites;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS tiles.sites")
