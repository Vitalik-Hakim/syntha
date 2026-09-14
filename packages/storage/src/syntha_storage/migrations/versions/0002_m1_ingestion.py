"""M1 ingestion schema: print dedup key, per-market fees, collections, recorder tables.

Every change here follows a verified API finding in docs/DATA_NOTES.md.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSTZ = sa.DateTime(timezone=True)
PRICE = sa.Numeric(8, 6)
MONEY = sa.Numeric(18, 6)

RECORDER_HYPERTABLES = ("orderbook_snapshots", "orderbook_deltas")


def upgrade() -> None:
    # --- markets: per-market fee schedule, creation/close times, UMA question id
    op.add_column("markets", sa.Column("question_id", sa.Text))
    op.add_column("markets", sa.Column("fee_type", sa.Text))
    op.add_column("markets", sa.Column("fee_rate", PRICE))
    op.add_column("markets", sa.Column("fee_exponent", sa.Integer))
    op.add_column("markets", sa.Column("created_at", TSTZ))
    op.add_column("markets", sa.Column("closed_time", TSTZ))  # ENGINE-RESTRICTED

    # --- trade_prints: natural dedup key (hypertable: unique index must include ts)
    op.add_column("trade_prints", sa.Column("print_key", sa.Text, nullable=False))
    op.create_unique_constraint("uq_trade_prints_print_key_ts", "trade_prints", ["print_key", "ts"])

    # --- fee_schedules: keyed by Polymarket feeType, not category (table is empty until seeded)
    op.drop_column("fee_schedules", "category")
    op.drop_column("fee_schedules", "theta")
    op.add_column("fee_schedules", sa.Column("fee_type", sa.Text, nullable=False))
    op.add_column("fee_schedules", sa.Column("rate", PRICE, nullable=False))
    op.add_column("fee_schedules", sa.Column("exponent", sa.Integer, nullable=False))
    op.add_column("fee_schedules", sa.Column("applies_to", sa.Text, nullable=False))
    op.create_unique_constraint(
        "uq_fee_schedules_venue_fee_type_effective_from",
        "fee_schedules",
        ["venue", "fee_type", "effective_from"],
    )

    # --- dataset_versions: machine-readable data-quality report
    op.add_column("dataset_versions", sa.Column("quality", pg.JSONB))

    # --- collections
    op.create_table(
        "collections",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("definition", pg.JSONB, nullable=False),
        sa.Column("created_at", TSTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_collections"),
        sa.UniqueConstraint("name", name="uq_collections_name"),
    )
    op.create_table(
        "collection_markets",
        sa.Column("collection_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("condition_id", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("collection_id", "condition_id", name="pk_collection_markets"),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_collection_markets_collection_id_collections",
            ondelete="CASCADE",
        ),
    )

    # --- recorder
    op.add_column("orderbook_snapshots", sa.Column("condition_id", sa.Text))
    op.add_column("orderbook_snapshots", sa.Column("book_hash", sa.Text))
    op.add_column("orderbook_snapshots", sa.Column("received_at", TSTZ))
    op.create_table(
        "orderbook_deltas",
        sa.Column("id", sa.BigInteger, nullable=False, autoincrement=True),
        sa.Column("ts", TSTZ, nullable=False),
        sa.Column("clob_token_id", sa.Text, nullable=False),
        sa.Column("condition_id", sa.Text),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("price", PRICE, nullable=False),
        sa.Column("size", MONEY, nullable=False),
        sa.Column("best_bid", PRICE),
        sa.Column("best_ask", PRICE),
        sa.Column("received_at", TSTZ),
        sa.PrimaryKeyConstraint("id", "ts", name="pk_orderbook_deltas"),
    )
    op.create_index(
        "ix_orderbook_deltas_clob_token_id_ts", "orderbook_deltas", ["clob_token_id", "ts"]
    )

    calls = "\n".join(
        f"    PERFORM create_hypertable('{t}', 'ts', chunk_time_interval => INTERVAL '1 day',"
        " create_default_indexes => FALSE);"
        for t in RECORDER_HYPERTABLES
    )
    op.execute(
        f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
{calls}
  ELSE
    RAISE WARNING 'timescaledb not installed: {", ".join(RECORDER_HYPERTABLES)} are plain tables';
  END IF;
END $$;
"""
    )


def downgrade() -> None:
    op.drop_table("orderbook_deltas")
    op.drop_column("orderbook_snapshots", "received_at")
    op.drop_column("orderbook_snapshots", "book_hash")
    op.drop_column("orderbook_snapshots", "condition_id")
    op.drop_table("collection_markets")
    op.drop_table("collections")
    op.drop_column("dataset_versions", "quality")
    # fee_schedules holds seed data rebuilt by `make rebuild-normalized`; the old shape can't
    # represent it, so it is cleared rather than lossily converted.
    op.execute("DELETE FROM fee_schedules")
    op.drop_constraint(
        "uq_fee_schedules_venue_fee_type_effective_from", "fee_schedules", type_="unique"
    )
    for col in ("applies_to", "exponent", "rate", "fee_type"):
        op.drop_column("fee_schedules", col)
    op.add_column("fee_schedules", sa.Column("theta", PRICE, nullable=False))
    op.add_column("fee_schedules", sa.Column("category", sa.Text, nullable=False))
    op.drop_constraint("uq_trade_prints_print_key_ts", "trade_prints", type_="unique")
    op.drop_column("trade_prints", "print_key")
    for col in ("closed_time", "created_at", "fee_exponent", "fee_rate", "fee_type", "question_id"):
        op.drop_column("markets", col)
