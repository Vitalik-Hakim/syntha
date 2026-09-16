"""M9: `census_observations.fill_first_10m` — the earliest taker-BUY print at or below
mid + 1 tick in the 10-minute window, a robustness fill next to the registered window VWAP
(docs/DATA_NOTES.md, "M9 discovery").

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("census_observations", sa.Column("fill_first_10m", sa.Numeric(8, 6)))


def downgrade() -> None:
    op.drop_column("census_observations", "fill_first_10m")
