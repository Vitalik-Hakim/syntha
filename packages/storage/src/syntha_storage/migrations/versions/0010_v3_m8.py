"""M8 (PRD v3 §3): baskets, wallets, recorder watchlist v2.

- `trade_prints.proxy_wallet`: the taker's proxy wallet from `/v2/trades` (raw layer has it;
  filled by the next `make rebuild-normalized`). Pseudonymous public addresses; never published.
- `bt_trades.basket_id` / `basket_role` (`LEG` | `SUMMARY`): all-or-nothing baskets (E3).
- `recorder_watchlist`: the designed, stratified watchlist (E7), point-in-time by `added_at`.
- `orderbook_deltas.event_type` (`price_change` | `trade`; NULL on rows recorded before this
  revision means `price_change`) and `fee_rate_bps` from `last_trade_price` events, the only
  per-fill fee record Polymarket publishes.
- Recorder compression after 1 day instead of 2 (E7 volume budget).

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSTZ = sa.DateTime(timezone=True)
RECORDER_HYPERTABLES = ("orderbook_snapshots", "orderbook_deltas")


def _compression_policy(interval: str) -> str:
    body = "\n".join(
        f"""    PERFORM remove_compression_policy('{t}', if_exists => TRUE);
    PERFORM add_compression_policy('{t}', INTERVAL '{interval}', if_not_exists => TRUE);"""
        for t in RECORDER_HYPERTABLES
    )
    return f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_name IN ('orderbook_snapshots', 'orderbook_deltas')) THEN
{body}
  END IF;
EXCEPTION WHEN undefined_table THEN
  RAISE WARNING 'timescaledb not installed: recorder compression policy unchanged';
END $$;
"""


def upgrade() -> None:
    op.add_column("trade_prints", sa.Column("proxy_wallet", sa.Text))
    op.add_column("bt_trades", sa.Column("basket_id", sa.Text))
    op.add_column("bt_trades", sa.Column("basket_role", sa.Text))
    op.add_column("orderbook_deltas", sa.Column("event_type", sa.Text))
    op.add_column("orderbook_deltas", sa.Column("fee_rate_bps", sa.Numeric(10, 4)))
    op.create_table(
        "recorder_watchlist",
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text),
        sa.Column("stratum", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("added_at", TSTZ, nullable=False),
        sa.Column("removed_at", TSTZ),
        sa.PrimaryKeyConstraint("clob_token_id", "added_at", name="pk_recorder_watchlist"),
    )
    op.execute(_compression_policy("1 day"))


def downgrade() -> None:
    op.execute(_compression_policy("2 days"))
    op.drop_table("recorder_watchlist")
    op.drop_column("orderbook_deltas", "fee_rate_bps")
    op.drop_column("orderbook_deltas", "event_type")
    op.drop_column("bt_trades", "basket_role")
    op.drop_column("bt_trades", "basket_id")
    op.drop_column("trade_prints", "proxy_wallet")
