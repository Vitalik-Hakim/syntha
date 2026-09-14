"""M3: persisted backtest runs (run id sequence, queue metadata, ledger).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Run ids are BT-YYYYMMDD-NNNNN (PRD §7); NNNNN comes from this sequence.
    op.execute("CREATE SEQUENCE backtest_run_seq")
    op.add_column(
        "backtest_runs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column("backtest_runs", sa.Column("strategy_name", sa.Text))
    op.add_column("backtest_runs", sa.Column("collection", sa.Text))
    op.add_column("backtest_runs", sa.Column("manifest", pg.JSONB))
    op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])
    op.create_unique_constraint("uq_bt_trades_run_id_seq", "bt_trades", ["run_id", "seq"])
    op.create_table(
        "bt_ledger",
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("running_cash", sa.Numeric(18, 6), nullable=False),
        sa.Column("ref", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("run_id", "seq", name="pk_bt_ledger"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="fk_bt_ledger_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("bt_ledger")
    op.drop_constraint("uq_bt_trades_run_id_seq", "bt_trades", type_="unique")
    op.drop_index("ix_backtest_runs_created_at", table_name="backtest_runs")
    for col in ("manifest", "collection", "strategy_name", "created_at"):
        op.drop_column("backtest_runs", col)
    op.execute("DROP SEQUENCE backtest_run_seq")
