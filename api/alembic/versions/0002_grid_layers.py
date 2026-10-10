"""Phase 1 tables: AOI, admin units, substations, lines, buildings, users, settings, plus map views.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aoi (
            id          serial PRIMARY KEY,
            name        text NOT NULL DEFAULT 'default',
            built_at    timestamptz NOT NULL DEFAULT now(),
            note        text,
            geom        geometry(MultiPolygon, 2180) NOT NULL
        );
        CREATE INDEX aoi_geom_gix ON aoi USING gist (geom);

        CREATE TABLE admin_units (
            teryt       text PRIMARY KEY,
            level       text NOT NULL CHECK (level IN ('voivodeship', 'powiat', 'gmina')),
            name        text NOT NULL,
            parent_teryt text,
            geom        geometry(MultiPolygon, 2180) NOT NULL
        );
        CREATE INDEX admin_units_geom_gix ON admin_units USING gist (geom);
        CREATE INDEX admin_units_level_ix ON admin_units (level);

        CREATE TABLE substations (
            osm_id      bigint PRIMARY KEY,
            name        text,
            operator    text,
            dso         text,                    -- enea | energa | tauron | pge | pse | NULL
            foreign_operator boolean NOT NULL DEFAULT false,  -- named, but neither a DSO nor PSE
            voltage_tag text,
            voltages    integer[] NOT NULL DEFAULT '{}',
            cls         text NOT NULL CHECK (cls IN ('GPZ_110_SN', 'NODE_220_400', 'OTHER', 'UNKNOWN_VOLTAGE')),
            confidence  text NOT NULL CHECK (confidence IN ('verified', 'estimated', 'missing')),
            source_id   text NOT NULL DEFAULT 'osm_power',
            as_of       timestamptz,
            geom        geometry(Point, 2180) NOT NULL
        );
        CREATE INDEX substations_geom_gix ON substations USING gist (geom);
        CREATE INDEX substations_cls_ix ON substations (cls);

        CREATE TABLE lines (
            osm_id      bigint PRIMARY KEY,
            voltage     integer,                 -- highest voltage, volts
            circuits    integer,
            operator    text,
            dso         text,
            confidence  text NOT NULL CHECK (confidence IN ('verified', 'estimated', 'missing')),
            source_id   text NOT NULL DEFAULT 'osm_power',
            as_of       timestamptz,
            geom        geometry(LineString, 2180) NOT NULL
        );
        CREATE INDEX lines_geom_gix ON lines USING gist (geom);
        CREATE INDEX lines_voltage_ix ON lines (voltage);

        CREATE TABLE buildings (
            id          bigserial PRIMARY KEY,
            ext_id      text NOT NULL,
            source_id   text NOT NULL,           -- bdot10k | osm_land
            residential boolean NOT NULL DEFAULT false,
            as_of       timestamptz,
            geom        geometry(Polygon, 2180) NOT NULL,
            UNIQUE (source_id, ext_id)
        );
        CREATE INDEX buildings_geom_gix ON buildings USING gist (geom);

        CREATE TABLE users (
            id            serial PRIMARY KEY,
            email         text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            role          text NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
            created_at    timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE settings (
            key         text PRIMARY KEY,
            value       jsonb NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    # Map views for Martin (only the `tiles` schema is published). Geometry is exposed with its real SRID;
    # Martin reprojects to Web Mercator.
    op.execute(
        """
        CREATE VIEW tiles.aoi AS
            SELECT id, name, geom::geometry(MultiPolygon, 2180) AS geom FROM aoi;

        CREATE VIEW tiles.substations AS
            SELECT osm_id, name, operator, dso, cls, confidence, voltages, foreign_operator,
                   geom::geometry(Point, 2180) AS geom
            FROM substations;

        CREATE VIEW tiles.lines AS
            SELECT osm_id, voltage, circuits, operator, dso, confidence,
                   geom::geometry(LineString, 2180) AS geom
            FROM lines
            WHERE voltage >= 110000;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW IF EXISTS tiles.lines, tiles.substations, tiles.aoi;
        DROP TABLE IF EXISTS settings, users, buildings, lines, substations, admin_units, aoi;
        """
    )
