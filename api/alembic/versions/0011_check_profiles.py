"""Sprawdź działkę: verdicts for the two investor profiles (DC budowane, DC kontenerowe).

A nullable `profiles` column on `parcel_check_sites`: a check made before this revision has none and the page then
shows no profile cards. The shape is `{"built": {...}, "container": {...}}`, see `dclh_pipeline.profiles`.

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
    op.execute("ALTER TABLE parcel_check_sites ADD COLUMN profiles jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE parcel_check_sites DROP COLUMN IF EXISTS profiles")
