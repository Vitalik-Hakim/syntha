"""M4: near misses (PRD §13.2) — traces of bars where the strategy did not trade.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bt_near_misses",
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        # RULES_FAILED | BLOCKED_BY_UNIVERSE | BLOCKED_BY_CONSTRAINT
        sa.Column("kind", sa.Text, nullable=False),
        # closeness to firing (RULES_FAILED only; lower is nearer): a ranking, not a quantity
        sa.Column("score", sa.Numeric(12, 6)),
        sa.Column("failed_clauses", sa.Integer, nullable=False),
        sa.Column("blocked_by", sa.Text),
        sa.Column("trace", pg.JSONB, nullable=False),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_bt_near_misses"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="fk_bt_near_misses_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_bt_near_misses_run_id_condition_id", "bt_near_misses", ["run_id", "condition_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_bt_near_misses_run_id_condition_id", table_name="bt_near_misses")
    op.drop_table("bt_near_misses")
