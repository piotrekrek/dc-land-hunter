"""Sprawdź działkę: stored checks of single parcels, their results and the loader's time estimates.

`parcel_checks` is one request (one or more parcels) with its progress as a `steps` list. `parcel_check_sites` holds
one row per parcel with the same columns as `sites` (so the site card code reads both), plus the verdict, the
"Nie wiemy" list, the manual next steps and the survey-sheet geometry. Not the `sites` table: its writer deletes
by powiat. Parcel ids and geometry only; no owner data (brief 9.6). Checks are kept, not expired (owner, 24.10.2026).
`check_step_stats` records how long each loading step took, for the estimate shown while a check runs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parcel_checks (
            id            uuid PRIMARY KEY,
            requested_by  text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            started_at    timestamptz,
            finished_at   timestamptz,
            status        text NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
            inputs        jsonb NOT NULL,               -- what was asked: [{kind, parcel_id | lat, lon, text}]
            steps         jsonb NOT NULL DEFAULT '[]',  -- [{parcel, key, label_pl, status, seconds, finding_pl, error}]
            error         text,
            job_id        bigint
        );
        CREATE INDEX parcel_checks_created_ix ON parcel_checks (created_at DESC);
        CREATE INDEX parcel_checks_job_ix ON parcel_checks (job_id);

        CREATE TABLE parcel_check_sites (
            check_id        uuid NOT NULL REFERENCES parcel_checks (id) ON DELETE CASCADE,
            n               integer NOT NULL,            -- position in the request, from 0
            site_id         text NOT NULL,
            powiat          text NOT NULL,
            parcel_ids      text[] NOT NULL,
            zone_id         text,
            area_ha         double precision NOT NULL,
            compactness     double precision NOT NULL,
            estimated_mw    double precision NOT NULL,
            score           double precision NOT NULL,
            upside_score    double precision NOT NULL,
            completeness    double precision NOT NULL CHECK (completeness >= 0 AND completeness <= 1),
            excluded        boolean NOT NULL,
            grid_badge      text NOT NULL,
            large_campus    boolean NOT NULL,
            split_suggested boolean NOT NULL,
            flags           text[] NOT NULL DEFAULT '{}',
            detail          jsonb NOT NULL,              -- {score, features}, as in sites.detail
            scored_at       timestamptz NOT NULL,
            geom            geometry(Geometry, 2180) NOT NULL,
            verdict         jsonb NOT NULL,              -- {kind, text_pl}
            outside_aoi     boolean NOT NULL DEFAULT false,
            unknowns        jsonb NOT NULL DEFAULT '[]',
            next_steps      jsonb NOT NULL DEFAULT '[]',
            sheet           jsonb NOT NULL,
            layer_ages      jsonb NOT NULL DEFAULT '{}',
            parcel          jsonb NOT NULL,              -- gmina, obręb, powiat, registered area
            PRIMARY KEY (check_id, n)
        );

        CREATE TABLE check_step_stats (
            id        bigserial PRIMARY KEY,
            step_key  text NOT NULL,
            warm      boolean NOT NULL,                  -- every answer came from the response cache
            seconds   double precision NOT NULL,
            at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX check_step_stats_key_ix ON check_step_stats (step_key, warm, at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS check_step_stats, parcel_check_sites, parcel_checks")
