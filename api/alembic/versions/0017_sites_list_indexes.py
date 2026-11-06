"""Partial indexes for the site list: the kept standard sites (`kind = 'standard' AND NOT excluded`).

The list counts the matching sites on every request. Without these the count read the whole table (130k rows,
59% of them excluded or small plots), about 300 ms. Measured on production data in November 2026:

- the count with the default filters: 295 ms → 3 ms (an index-only scan of `sites_list_ix`);
- a voivodeship filter sorted by upside: 120 ms → 3 ms.

`sites_list_ix` covers the columns the list filters on, so the count reads only the index. The sort indexes
end with `site_id`, the list's tie-break. Sorting by score keeps using `sites_score_ix`.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEPT = "WHERE kind = 'standard' AND NOT excluded"
SORT_INDEXES = {
    "sites_list_upside_ix": "upside_score",
    "sites_list_completeness_ix": "completeness",
    "sites_list_area_ix": "area_ha",
    "sites_list_mw_ix": "estimated_mw",
}


def upgrade() -> None:
    op.execute(
        "CREATE INDEX sites_list_ix ON sites (powiat)"
        f" INCLUDE (estimated_mw, area_ha, verdict_built, verdict_container) {KEPT}"
    )
    for name, column in SORT_INDEXES.items():
        op.execute(f"CREATE INDEX {name} ON sites ({column} DESC, site_id) {KEPT}")


def downgrade() -> None:
    for name in ["sites_list_ix", *SORT_INDEXES]:
        op.execute(f"DROP INDEX IF EXISTS {name}")
