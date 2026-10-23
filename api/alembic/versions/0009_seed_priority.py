"""Move the starter priority powiats from the crontab into the saved list shown in the Powiaty tab.

The three were set in `infra/crontab` (PR #90): 0805 powiat słubicki, 3021 powiat poznański (Plewiska GPZ) and
1465 Warszawa (Bielany). In the table, the tab shows them and can change them, and its "next up" preview counts them.
The planner still skips any that already have a successful search.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO powiat_priority (teryt, added_by)
        VALUES ('0805', 'seed'), ('3021', 'seed'), ('1465', 'seed')
        ON CONFLICT (teryt) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM powiat_priority WHERE added_by = 'seed'")
