"""Powiats the team wants searched first, set from the Powiaty tab.

The nightly planner (`plan_searches`) puts these before its rotation until each has one successful search.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE powiat_priority (
            teryt     text PRIMARY KEY,                -- powiat code, e.g. 0805
            added_by  text,                            -- e-mail of the user who saved it
            added_at  timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS powiat_priority")
