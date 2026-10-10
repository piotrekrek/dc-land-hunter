"""Initial schema: PostGIS plus sources, snapshots, runs and jobs.

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    # Martin publishes only this schema (views/functions for the map). See infra/martin/config.yaml.
    op.execute("CREATE SCHEMA IF NOT EXISTS tiles")

    op.create_table(
        "sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("last_attempt_at", TZ),
        sa.Column("last_success_at", TZ),
        sa.Column("last_error", sa.Text),
        sa.Column("row_count", sa.Integer),
        sa.Column("stale", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("discovery_status", sa.String(16)),
        sa.Column("discovery_checked_at", TZ),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("fetched_at", TZ, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("content_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("good", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_snapshots_source_fetched", "snapshots", ["source_id", "fetched_at"])
    op.create_table(
        "runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TZ),
        sa.Column("summary", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(255)),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TZ),
        sa.Column("finished_at", TZ),
        sa.Column("error", sa.Text),
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("runs.id")),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("runs")
    op.drop_table("snapshots")
    op.drop_table("sources")
    op.execute("DROP SCHEMA IF EXISTS tiles CASCADE")
