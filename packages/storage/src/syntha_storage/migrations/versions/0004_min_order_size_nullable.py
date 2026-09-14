"""Allow unknown minimum order size.

Some closed markets report no minimum order size in either Gamma (`orderMinSize: null`) or the
CLOB (`/clob-markets` has no `mos`). Dropping them would remove markets from the universe (PRD
§5.1 rule 6), and inventing a value would break "never invent data". The column is nullable, and
the engine must treat an unknown minimum as unexecutable.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("markets", "min_order_size", existing_type=sa.Numeric(18, 6), nullable=True)


def downgrade() -> None:
    # Normalized rows are rebuildable (`make rebuild-normalized`); the old schema can't hold these.
    op.execute(
        "DELETE FROM outcomes WHERE condition_id IN "
        "(SELECT condition_id FROM markets WHERE min_order_size IS NULL)"
    )
    op.execute("DELETE FROM markets WHERE min_order_size IS NULL")
    op.alter_column("markets", "min_order_size", existing_type=sa.Numeric(18, 6), nullable=False)
