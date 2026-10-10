"""Login sessions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sessions (
            token_hash  varchar(64) PRIMARY KEY,
            user_id     integer NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at  timestamptz NOT NULL,
            expires_at  timestamptz NOT NULL
        );
        CREATE INDEX ix_sessions_user ON sessions (user_id);
        CREATE INDEX ix_sessions_expires ON sessions (expires_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sessions")
