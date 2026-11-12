"""Power evidence per site: how firmly the grid connection is documented (RTB export).

One row per site, typed in by the team, never derived. A site without a row reads `rumour`. Like `project_cards`
there is no foreign key to `sites`: the nightly search rewrites that table by powiat and the evidence must survive it.
There is no free-text column on purpose (personal-data rule, brief 9.6): the document is a date and a link.

Revision ID: 0021
Revises: 0019
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE power_evidence (
            site_id            text PRIMARY KEY,
            level              text NOT NULL CHECK (level IN (
                'rumour', 'informal_opinion', 'application_filed', 'conditions_issued', 'agreement_signed')),
            document_date      date,
            conditions_expiry  date,
            attachment_url     text CHECK (attachment_url IS NULL OR attachment_url ~* '^https?://'),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            updated_by         integer REFERENCES users (id) ON DELETE SET NULL,
            CHECK (conditions_expiry IS NULL OR level IN ('conditions_issued', 'agreement_signed'))
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS power_evidence")
