"""Sites belong to a powiat, and each powiat's search attempts are recorded (PLAN.md, Phase 1.5).

A powiat search replaces only its own sites. Rows written before this revision came from the pilot run
on powiat 1421 (Pruszków), the only search run so far, so they are tagged with it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sites ADD COLUMN powiat text;
        UPDATE sites SET powiat = '1421' WHERE powiat IS NULL;
        ALTER TABLE sites ALTER COLUMN powiat SET NOT NULL;
        CREATE INDEX sites_powiat_ix ON sites (powiat);

        CREATE TABLE search_runs (
            teryt            text PRIMARY KEY,           -- powiat code, e.g. 1421
            last_attempt_at  timestamptz NOT NULL,       -- successful or not; the planner picks the stalest
            last_ok_at       timestamptz,                -- last successful search
            status           text NOT NULL,              -- 'ok' | 'failed'
            sites            integer,
            seconds          double precision,
            error            text
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_runs")
    op.execute("DROP INDEX IF EXISTS sites_powiat_ix")
    op.execute("ALTER TABLE sites DROP COLUMN IF EXISTS powiat")
