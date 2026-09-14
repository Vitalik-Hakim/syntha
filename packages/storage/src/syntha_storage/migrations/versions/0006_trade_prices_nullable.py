"""Allow unknown entry prices on refused trade rows.

An UNEXECUTABLE signal (no prints, stale price, unknown minimum size) has no reference or fill
price. Storing 0 there invented a price, and the blotter showed it as a real quote. NULL records
that no price existed.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRICE = sa.Numeric(8, 6)


def upgrade() -> None:
    for column in ("entry_price", "entry_ref_price"):
        op.alter_column("bt_trades", column, existing_type=PRICE, nullable=True)
    op.execute(
        "UPDATE bt_trades SET entry_price = NULL, entry_ref_price = NULL "
        "WHERE fill_status = 'UNEXECUTABLE' AND entry_ref_price = 0"
    )


def downgrade() -> None:
    op.execute("UPDATE bt_trades SET entry_price = COALESCE(entry_price, 0)")
    op.execute("UPDATE bt_trades SET entry_ref_price = COALESCE(entry_ref_price, 0)")
    for column in ("entry_price", "entry_ref_price"):
        op.alter_column("bt_trades", column, existing_type=PRICE, nullable=False)
