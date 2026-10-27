"""Small plots (up to 2 ha) where the grid, not the land, carries the 60 MW (PLAN.md, "Small plots with 60 MW grid").

`kind` tells the ranked sites (`standard`, 10 ha or more) from the small plots (`small`); `grid60` is the small
plot's grid tier (`strong` | `possible`), NULL on standard sites. Until the list and the map learn about small
plots, the map layers and the coverage view show standard sites only, so this revision changes nothing on screen.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Edge of the Web Mercator world in metres (as in 0007).
WORLD_M = "40075016.6856"


def _cluster_cells(where: str) -> str:
    """Migration 0007's `tile_site_cluster_cells`, with an extra condition on the sites."""
    return f"""
        CREATE OR REPLACE FUNCTION public.tile_site_cluster_cells(z integer, x integer, y integer)
        RETURNS TABLE (n bigint, best double precision, px double precision, py double precision)
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            WITH env AS (SELECT ST_TileEnvelope(z, x, y) AS g, {WORLD_M} / (2 ^ z) / 8 AS cell),
            pts AS (
                SELECT ST_X(p) AS px, ST_Y(p) AS py, s.score, s.excluded
                FROM sites s, env,
                     LATERAL (SELECT ST_Transform(ST_Centroid(s.geom), 3857) AS p) c
                WHERE (z < 5 OR s.geom && ST_Transform(env.g, 2180)){where}
                  AND ST_X(p) >= ST_XMin(env.g) AND ST_X(p) < ST_XMax(env.g)
                  AND ST_Y(p) >= ST_YMin(env.g) AND ST_Y(p) < ST_YMax(env.g)
            )
            SELECT count(*)::bigint,
                   max(pts.score) FILTER (WHERE NOT pts.excluded),
                   avg(pts.px), avg(pts.py)
            FROM pts, env
            GROUP BY floor(pts.px / env.cell), floor(pts.py / env.cell)
        $$;
        """


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sites
            ADD COLUMN kind text NOT NULL DEFAULT 'standard' CHECK (kind IN ('standard', 'small')),
            ADD COLUMN grid60 text CHECK (grid60 IN ('strong', 'possible'));
        CREATE INDEX sites_kind_ix ON sites (kind);

        CREATE OR REPLACE VIEW tiles.sites AS
            SELECT site_id, powiat, score, excluded, area_ha, estimated_mw,
                   geom::geometry(Geometry, 2180) AS geom
            FROM sites
            WHERE kind = 'standard';
        """
    )
    op.execute(_cluster_cells("\n                  AND s.kind = 'standard'"))


def downgrade() -> None:
    op.execute(_cluster_cells(""))
    op.execute(
        """
        CREATE OR REPLACE VIEW tiles.sites AS
            SELECT site_id, powiat, score, excluded, area_ha, estimated_mw,
                   geom::geometry(Geometry, 2180) AS geom
            FROM sites;
        """
    )
    # The view reads only the old columns, so the columns can go after it is restored.
    op.execute("DROP INDEX IF EXISTS sites_kind_ix")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS grid60, DROP COLUMN IF EXISTS kind")
