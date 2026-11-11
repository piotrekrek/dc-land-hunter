"""`site_history`: what changed in the kept sites, for "what changed since last week?".

A powiat search replaces that powiat's rows in `sites`, so without this table nothing remembers the earlier state.
One row per event, written by `write_sites` only when something changed: `new`, `changed` (score or per-profile
verdict) and `dropped`. `baseline` rows are the starting state, seeded here from the current `sites`, so the first
nightly run after the deploy records real differences instead of "everything is new". They also date the start of
the history (`min(recorded_at)`). Only kept standard sites are tracked (`kind = 'standard' AND NOT excluded`).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE site_history (
            id                    bigserial PRIMARY KEY,
            site_id               text NOT NULL,
            event                 text NOT NULL CHECK (event IN ('baseline', 'new', 'changed', 'dropped')),
            powiat                text NOT NULL,
            gmina                 text,                    -- filled on `dropped` (the site's row is gone)
            area_ha               double precision,
            estimated_mw          double precision,
            score                 double precision,        -- the values after the event (last known on `dropped`)
            verdict_built         text,
            verdict_container     text,
            prev_score            double precision,        -- `changed` only
            prev_verdict_built    text,
            prev_verdict_container text,
            scored_at             timestamptz NOT NULL,    -- when the run scored the site
            recorded_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX site_history_scored_ix ON site_history (scored_at);
        CREATE INDEX site_history_site_ix ON site_history (site_id, scored_at);
        INSERT INTO site_history (
            site_id, event, powiat, area_ha, estimated_mw, score, verdict_built, verdict_container, scored_at
        )
        SELECT site_id, 'baseline', powiat, area_ha, estimated_mw, score, verdict_built, verdict_container,
               scored_at
        FROM sites WHERE kind = 'standard' AND NOT excluded;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_history")
