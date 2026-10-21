"""Aggregated site bubbles for the zoomed-out map (brief 8.1: the map shows the shortlist).

Below zoom 8 single sites are too small to draw, so Martin serves them as counted bubbles instead:
sites are grouped on a fixed grid (a tile is 8 x 8 cells, aligned to the Web Mercator origin), so a cell
never straddles two tiles and a bubble is drawn once. Below zoom 5 a tile is as big as Europe or the world and
cannot be turned into an EPSG:2180 box, so every site is scanned (cheap: few such tiles are ever asked for). The grouping lives in a plain function that the
tests call; the MVT function in `tiles` (the only schema Martin publishes) wraps it.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Edge of the Web Mercator world in metres.
WORLD_M = "40075016.6856"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.tile_site_cluster_cells(z integer, x integer, y integer)
        RETURNS TABLE (n bigint, best double precision, px double precision, py double precision)
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            WITH env AS (SELECT ST_TileEnvelope(z, x, y) AS g, {WORLD_M} / (2 ^ z) / 8 AS cell),
            pts AS (
                SELECT ST_X(p) AS px, ST_Y(p) AS py, s.score, s.excluded
                FROM sites s, env,
                     LATERAL (SELECT ST_Transform(ST_Centroid(s.geom), 3857) AS p) c
                WHERE (z < 5 OR s.geom && ST_Transform(env.g, 2180))
                  AND ST_X(p) >= ST_XMin(env.g) AND ST_X(p) < ST_XMax(env.g)
                  AND ST_Y(p) >= ST_YMin(env.g) AND ST_Y(p) < ST_YMax(env.g)
            )
            SELECT count(*)::bigint,
                   max(pts.score) FILTER (WHERE NOT pts.excluded),
                   avg(pts.px), avg(pts.py)
            FROM pts, env
            GROUP BY floor(pts.px / env.cell), floor(pts.py / env.cell)
        $$;

        CREATE FUNCTION tiles.site_clusters(z integer, x integer, y integer)
        RETURNS bytea
        LANGUAGE sql STABLE PARALLEL SAFE AS $$
            SELECT COALESCE(ST_AsMVT(q, 'site_clusters', 4096, 'geom'), ''::bytea)
            FROM (
                SELECT c.n, c.best,
                       ST_AsMVTGeom(ST_SetSRID(ST_MakePoint(c.px, c.py), 3857),
                                    ST_TileEnvelope(z, x, y), 4096, 64, true) AS geom
                FROM public.tile_site_cluster_cells(z, x, y) c
            ) q
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS tiles.site_clusters(integer, integer, integer);
        DROP FUNCTION IF EXISTS public.tile_site_cluster_cells(integer, integer, integer);
        """
    )
