"""ORM mapping of PRD §7. The migration in migrations/versions is the source of truth for DDL;
tests/test_migrations.py asserts the two agree.

Conventions (PRD §7): timestamps are timestamptz (UTC), money numeric(18,6), prices
numeric(8,6). Never floats for cash.

Columns tagged ENGINE_RESTRICTED (resolution outcome and anything derived from it) must be
stripped by the repository layer before data reaches the strategy evaluator; they travel only in
the SettlementTable (PRD §5.1 rule 4).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ENGINE_RESTRICTED: Final = "engine_restricted"
_RESTRICTED: Final = {ENGINE_RESTRICTED: True}

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TSTZ = DateTime(timezone=True)
PRICE = Numeric(8, 6)
MONEY = Numeric(18, 6)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ------------------------------------------------------------------ market data


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    slug: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime | None] = mapped_column(TSTZ)
    ingested_at: Mapped[datetime] = mapped_column(TSTZ)


class Market(Base):
    __tablename__ = "markets"

    condition_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[str | None] = mapped_column(Text, ForeignKey("events.id"))
    slug: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    neg_risk: Mapped[bool] = mapped_column(Boolean, server_default="false")
    tick_size: Mapped[Decimal] = mapped_column(PRICE)
    min_order_size: Mapped[Decimal] = mapped_column(MONEY)
    fees_enabled: Mapped[bool | None] = mapped_column(Boolean)
    open_ts: Mapped[datetime | None] = mapped_column(TSTZ)
    end_date: Mapped[datetime | None] = mapped_column(TSTZ)
    resolved_at: Mapped[datetime | None] = mapped_column(TSTZ, info=_RESTRICTED)
    resolution: Mapped[str | None] = mapped_column(Text, info=_RESTRICTED)
    final_volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), info=_RESTRICTED)
    ingested_at: Mapped[datetime] = mapped_column(TSTZ)


class Outcome(Base):
    __tablename__ = "outcomes"
    __table_args__ = (UniqueConstraint("condition_id", "outcome_index"),)

    clob_token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    condition_id: Mapped[str] = mapped_column(Text, ForeignKey("markets.condition_id"))
    outcome_index: Mapped[int] = mapped_column(Integer)
    outcome_name: Mapped[str] = mapped_column(Text)


class PriceBar(Base):
    """Timescale hypertable on ts, 7-day chunks."""

    __tablename__ = "price_bars"

    clob_token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TSTZ, primary_key=True)
    price: Mapped[Decimal] = mapped_column(PRICE)
    is_stale: Mapped[bool] = mapped_column(Boolean, server_default="false")
    staleness_minutes: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)


class TradePrint(Base):
    """Timescale hypertable on ts, 7-day chunks.

    Primary key is (id, ts) rather than the PRD's (id): Timescale requires the partitioning
    column in every unique index. See docs/DATA_NOTES.md.
    """

    __tablename__ = "trade_prints"
    __table_args__ = (Index(None, "clob_token_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TSTZ, primary_key=True)
    condition_id: Mapped[str] = mapped_column(Text)
    clob_token_id: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(PRICE)
    size: Mapped[Decimal] = mapped_column(MONEY)
    side: Mapped[str] = mapped_column(Text)


class OrderbookSnapshot(Base):
    """Populated ONLY by the live recorder. Empty for historical periods (PRD §4.2)."""

    __tablename__ = "orderbook_snapshots"

    clob_token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TSTZ, primary_key=True)
    bids: Mapped[list[list[str]]] = mapped_column(JSONB)
    asks: Mapped[list[list[str]]] = mapped_column(JSONB)


class FeeSchedule(Base):
    __tablename__ = "fee_schedules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    theta: Mapped[Decimal] = mapped_column(PRICE)
    effective_from: Mapped[datetime] = mapped_column(TSTZ)
    effective_to: Mapped[datetime | None] = mapped_column(TSTZ)
    source_url: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    built_at: Mapped[datetime] = mapped_column(TSTZ)
    content_hash: Mapped[str] = mapped_column(Text)
    n_markets: Mapped[int | None] = mapped_column(Integer)
    n_tokens: Mapped[int | None] = mapped_column(Integer)
    n_bars: Mapped[int | None] = mapped_column(BigInteger)
    n_prints: Mapped[int | None] = mapped_column(BigInteger)
    coverage_start: Mapped[datetime | None] = mapped_column(TSTZ)
    coverage_end: Mapped[datetime | None] = mapped_column(TSTZ)
    notes: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ strategies and runs


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TSTZ)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("strategy_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    strategy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("strategies.id"))
    version: Mapped[int] = mapped_column(Integer)
    dsl: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dsl_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TSTZ)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # BT-YYYYMMDD-NNNNN
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.id")
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dataset_versions.id")
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(Text)
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TSTZ)
    finished_at: Mapped[datetime | None] = mapped_column(TSTZ)
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSONB)


class BtTrade(Base):
    __tablename__ = "bt_trades"
    __table_args__ = (Index(None, "run_id", "entry_ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    condition_id: Mapped[str] = mapped_column(Text)
    clob_token_id: Mapped[str] = mapped_column(Text)
    side: Mapped[str] = mapped_column(Text)
    signal_ts: Mapped[datetime] = mapped_column(TSTZ)
    entry_ts: Mapped[datetime] = mapped_column(TSTZ)
    entry_price: Mapped[Decimal] = mapped_column(PRICE)
    entry_ref_price: Mapped[Decimal] = mapped_column(PRICE)
    shares: Mapped[Decimal] = mapped_column(MONEY)
    notional: Mapped[Decimal] = mapped_column(MONEY)
    entry_fee: Mapped[Decimal] = mapped_column(MONEY)
    exit_ts: Mapped[datetime | None] = mapped_column(TSTZ)
    exit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    exit_fee: Mapped[Decimal | None] = mapped_column(MONEY)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    gross_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    fees_total: Mapped[Decimal | None] = mapped_column(MONEY)
    slippage_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    net_pnl: Mapped[Decimal | None] = mapped_column(MONEY)
    return_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    holding_seconds: Mapped[int | None] = mapped_column(Integer)
    entry_rule_trace: Mapped[dict[str, Any]] = mapped_column(JSONB)
    exit_rule_trace: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fill_status: Mapped[str] = mapped_column(Text)


class BtEquity(Base):
    __tablename__ = "bt_equity"

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(TSTZ, primary_key=True)
    cash: Mapped[Decimal] = mapped_column(MONEY)
    positions_value: Mapped[Decimal] = mapped_column(MONEY)
    equity: Mapped[Decimal] = mapped_column(MONEY)
    open_positions: Mapped[int] = mapped_column(Integer)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    exposure_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))
