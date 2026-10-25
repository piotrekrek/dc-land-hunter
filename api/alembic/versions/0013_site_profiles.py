"""Investor profile verdicts on the ranked sites.

`profiles` is the full verdict of both profiles (same shape as `parcel_check_sites.profiles`); `verdict_built` and
`verdict_container` repeat the category so the list can filter on it. Rows scored before this revision are NULL until
`dclh reprofile` fills them from the stored features, without a new search.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sites
            ADD COLUMN profiles jsonb,
            ADD COLUMN verdict_built text,
            ADD COLUMN verdict_container text;
        CREATE INDEX sites_verdict_built_ix ON sites (verdict_built);
        CREATE INDEX sites_verdict_container_ix ON sites (verdict_container);
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE sites DROP COLUMN IF EXISTS profiles, DROP COLUMN IF EXISTS verdict_built, "
        "DROP COLUMN IF EXISTS verdict_container"
    )
