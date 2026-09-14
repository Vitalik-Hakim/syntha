"""Initial schema (PRD §7).

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSTZ = sa.DateTime(timezone=True)
PRICE = sa.Numeric(8, 6)
MONEY = sa.Numeric(18, 6)

HYPERTABLES = ("price_bars", "trade_prints")


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Text, nullable=False),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("tags", pg.ARRAY(sa.Text)),
        sa.Column("created_at", TSTZ),
        sa.Column("ingested_at", TSTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )

    op.create_table(
        "markets",
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("event_id", sa.Text),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("neg_risk", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("tick_size", PRICE, nullable=False),
        sa.Column("min_order_size", MONEY, nullable=False),
        sa.Column("fees_enabled", sa.Boolean),
        sa.Column("open_ts", TSTZ),
        sa.Column("end_date", TSTZ),
        sa.Column("resolved_at", TSTZ),  # ENGINE-RESTRICTED
        sa.Column("resolution", sa.Text),  # ENGINE-RESTRICTED
        sa.Column("final_volume", sa.Numeric(18, 2)),  # ENGINE-RESTRICTED
        sa.Column("ingested_at", TSTZ, nullable=False),
        sa.PrimaryKeyConstraint("condition_id", name="pk_markets"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_markets_event_id_events"),
    )

    op.create_table(
        "outcomes",
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("outcome_index", sa.Integer, nullable=False),
        sa.Column("outcome_name", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("clob_token_id", name="pk_outcomes"),
        sa.ForeignKeyConstraint(
            ["condition_id"],
            ["markets.condition_id"],
            name="fk_outcomes_condition_id_markets",
        ),
        sa.UniqueConstraint(
            "condition_id", "outcome_index", name="uq_outcomes_condition_id_outcome_index"
        ),
    )

    op.create_table(
        "price_bars",
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("ts", TSTZ, nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("is_stale", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("staleness_minutes", sa.Integer),
        sa.Column("source", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("clob_token_id", "ts", name="pk_price_bars"),
    )

    # PK is (id, ts), not (id): Timescale requires the partition column in unique indexes.
    op.create_table(
        "trade_prints",
        sa.Column("id", sa.BigInteger, nullable=False, autoincrement=True),
        sa.Column("ts", TSTZ, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("size", MONEY, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", "ts", name="pk_trade_prints"),
    )
    op.create_index("ix_trade_prints_clob_token_id_ts", "trade_prints", ["clob_token_id", "ts"])

    op.create_table(
        "orderbook_snapshots",
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("ts", TSTZ, nullable=False),
        sa.Column("bids", pg.JSONB, nullable=False),
        sa.Column("asks", pg.JSONB, nullable=False),
        sa.PrimaryKeyConstraint("clob_token_id", "ts", name="pk_orderbook_snapshots"),
    )

    op.create_table(
        "fee_schedules",
        sa.Column("id", sa.BigInteger, nullable=False, autoincrement=True),
        sa.Column("venue", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("theta", PRICE, nullable=False),
        sa.Column("effective_from", TSTZ, nullable=False),
        sa.Column("effective_to", TSTZ),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_fee_schedules"),
    )

    op.create_table(
        "dataset_versions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("built_at", TSTZ, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("n_markets", sa.Integer),
        sa.Column("n_tokens", sa.Integer),
        sa.Column("n_bars", sa.BigInteger),
        sa.Column("n_prints", sa.BigInteger),
        sa.Column("coverage_start", TSTZ),
        sa.Column("coverage_end", TSTZ),
        sa.Column("notes", sa.Text),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_versions"),
    )

    op.create_table(
        "strategies",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_at", TSTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_strategies"),
    )

    op.create_table(
        "strategy_versions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("dsl", pg.JSONB, nullable=False),
        sa.Column("dsl_hash", sa.Text, nullable=False),
        sa.Column("created_at", TSTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_versions"),
        sa.ForeignKeyConstraint(
            ["strategy_id"], ["strategies.id"], name="fk_strategy_versions_strategy_id_strategies"
        ),
        sa.UniqueConstraint(
            "strategy_id", "version", name="uq_strategy_versions_strategy_id_version"
        ),
    )

    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Text, nullable=False),
        sa.Column("strategy_version_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("config", pg.JSONB, nullable=False),
        sa.Column("config_hash", sa.Text, nullable=False),
        sa.Column("engine_version", sa.Text, nullable=False),
        sa.Column("seed", sa.BigInteger, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("started_at", TSTZ),
        sa.Column("finished_at", TSTZ),
        sa.Column("error", sa.Text),
        sa.Column("metrics", pg.JSONB),
        sa.Column("data_quality", pg.JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_backtest_runs"),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name="fk_backtest_runs_strategy_version_id_strategy_versions",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name="fk_backtest_runs_dataset_version_id_dataset_versions",
        ),
    )

    op.create_table(
        "bt_trades",
        sa.Column("id", sa.BigInteger, nullable=False, autoincrement=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("signal_ts", TSTZ, nullable=False),
        sa.Column("entry_ts", TSTZ, nullable=False),
        sa.Column("entry_price", PRICE, nullable=False),
        sa.Column("entry_ref_price", PRICE, nullable=False),
        sa.Column("shares", MONEY, nullable=False),
        sa.Column("notional", MONEY, nullable=False),
        sa.Column("entry_fee", MONEY, nullable=False),
        sa.Column("exit_ts", TSTZ),
        sa.Column("exit_price", PRICE),
        sa.Column("exit_fee", MONEY),
        sa.Column("exit_reason", sa.Text),
        sa.Column("gross_pnl", MONEY),
        sa.Column("fees_total", MONEY),
        sa.Column("slippage_cost", MONEY),
        sa.Column("net_pnl", MONEY),
        sa.Column("return_pct", sa.Numeric(12, 6)),
        sa.Column("holding_seconds", sa.Integer),
        sa.Column("entry_rule_trace", pg.JSONB, nullable=False),
        sa.Column("exit_rule_trace", pg.JSONB),
        sa.Column("fill_status", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bt_trades"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="fk_bt_trades_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_bt_trades_run_id_entry_ts", "bt_trades", ["run_id", "entry_ts"])

    op.create_table(
        "bt_equity",
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("ts", TSTZ, nullable=False),
        sa.Column("cash", MONEY, nullable=False),
        sa.Column("positions_value", MONEY, nullable=False),
        sa.Column("equity", MONEY, nullable=False),
        sa.Column("open_positions", sa.Integer, nullable=False),
        sa.Column("realized_pnl", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=False),
        sa.Column("exposure_pct", sa.Numeric(8, 4), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "ts", name="pk_bt_equity"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["backtest_runs.id"],
            name="fk_bt_equity_run_id_backtest_runs",
            ondelete="CASCADE",
        ),
    )

    # Hypertables. Tolerates plain Postgres (e.g. a local install without the extension) with a
    # warning; CI runs against the Timescale image and asserts the hypertables exist.
    hypertable_calls = "\n".join(
        f"    PERFORM create_hypertable('{t}', 'ts', chunk_time_interval => INTERVAL '7 days',"
        " create_default_indexes => FALSE);"
        for t in HYPERTABLES
    )
    op.execute(
        f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
{hypertable_calls}
  ELSE
    RAISE WARNING 'timescaledb not available: {", ".join(HYPERTABLES)} are plain tables';
  END IF;
END $$;
"""
    )


def downgrade() -> None:
    for table in (
        "bt_equity",
        "bt_trades",
        "backtest_runs",
        "strategy_versions",
        "strategies",
        "dataset_versions",
        "fee_schedules",
        "orderbook_snapshots",
        "trade_prints",
        "price_bars",
        "outcomes",
        "markets",
        "events",
    ):
        op.drop_table(table)
