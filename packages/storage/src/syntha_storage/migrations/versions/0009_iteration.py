"""M6: iteration — forked runs remember their parent, strategy versions carry a change note.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("parent_run_id", sa.Text))
    op.create_foreign_key(
        "fk_backtest_runs_parent_run_id_backtest_runs",
        "backtest_runs",
        "backtest_runs",
        ["parent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("strategy_versions", sa.Column("note", sa.Text))


def downgrade() -> None:
    op.drop_column("strategy_versions", "note")
    op.drop_constraint(
        "fk_backtest_runs_parent_run_id_backtest_runs", "backtest_runs", type_="foreignkey"
    )
    op.drop_column("backtest_runs", "parent_run_id")
