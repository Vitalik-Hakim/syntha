"""M5: the honesty layer's results (PRD §15) and scenario preset groups (PRD §11.6).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # bootstrap CIs, permutation test, random baseline, buy-and-hold benchmark
    op.add_column("backtest_runs", sa.Column("honesty", pg.JSONB))
    # "Run all three" presets share a group; preset is OPTIMISTIC | REALISTIC | CONSERVATIVE | CUSTOM
    op.add_column("backtest_runs", sa.Column("group_id", pg.UUID(as_uuid=True)))
    op.add_column("backtest_runs", sa.Column("preset", sa.Text))
    op.create_index("ix_backtest_runs_group_id", "backtest_runs", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_group_id", table_name="backtest_runs")
    for col in ("preset", "group_id", "honesty"):
        op.drop_column("backtest_runs", col)
