"""Query timing statistics (pg_stat_statements), to find slow queries from real use.

The library must also be preloaded (`shared_preload_libraries`, set on the db service in infra/docker-compose.yml).
Without the preload the extension still installs, but reading the view fails; nothing in the app reads it.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
