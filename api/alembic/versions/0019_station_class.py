"""Substations: whose station it is (operator_gpz | generator | customer | unknown) and why.

Wind and PV collector stations ("GPO …") and customers' own stations carry 110 kV too, but they are not a
connection point for a new load. The class is filled by `dclh load-grid`; rows loaded before this migration
read `unknown` until the grid is loaded again.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "substations",
        sa.Column("station_class", sa.String(16), nullable=False, server_default="unknown"),
    )
    op.add_column("substations", sa.Column("class_source", sa.String(24), nullable=True))
    op.add_column(
        "substations",
        sa.Column("class_confidence", sa.String(16), nullable=False, server_default="missing"),
    )


def downgrade() -> None:
    op.drop_column("substations", "class_confidence")
    op.drop_column("substations", "class_source")
    op.drop_column("substations", "station_class")
