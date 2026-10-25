"""Watches and project cards: what a user follows and where a site stands in the deal pipeline.

`watches` is per user. `project_cards` is one row per site, shared by the team (brief 8.1 "Moje projekty"), with the
brief's seven stages and a free-text owner note. The note is the only personal data the app keeps (brief 9.6): the
card shows a RODO notice and deleting the project row deletes the note with it. No foreign key to `sites`: the
nightly search rewrites that table by powiat, and a watch must survive it (a site that is gone just stops showing).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE watches (
            user_id     integer NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            site_id     text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, site_id)
        );
        CREATE INDEX watches_site_ix ON watches (site_id);

        CREATE TABLE project_cards (
            site_id     text PRIMARY KEY,
            stage       text NOT NULL CHECK (stage IN (
                'new', 'watching', 'analysis', 'owner_contact', 'negotiation', 'rejected', 'secured')),
            owner_note  text,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            updated_by  integer REFERENCES users (id) ON DELETE SET NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_cards, watches")
