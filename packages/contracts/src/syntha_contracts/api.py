"""HTTP API response models (PRD §14). The web app's TypeScript types are generated from the OpenAPI
schema built on these (`make schemas`), so the browser never hand-mirrors a shape.

Numbers the UI displays come from these payloads, which come from `backtest_runs`, `bt_trades`
and `bt_equity`. The browser formats them; it never computes a metric (PRD §0 rule 3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from syntha_contracts.backtest import BacktestRequest, ExecutionConfig, FeeConfig
from syntha_contracts.enums import RunStatus
from syntha_contracts.strategy import StrategyDSL


class BuiltinStrategy(BaseModel):
    key: str
    name: str
    description: str | None
    dsl: StrategyDSL


class ValidateStrategyRequest(BaseModel):
    dsl: dict[str, Any]
    bar_interval_minutes: int = 1


class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    dsl_hash: str | None = None
    features: list[str] = Field(default_factory=list)
    lookback_bars: int | None = None


class PresetInfo(BaseModel):
    name: Literal["OPTIMISTIC", "REALISTIC", "CONSERVATIVE"]
    label: str
    description: str
    execution: ExecutionConfig
    fees: FeeConfig


class CollectionInfo(BaseModel):
    name: str
    created_at: datetime
    markets: int
    markets_by_category: dict[str, int]
    definition: dict[str, Any]
    survivorship_note: str | None = None


class DatasetVersionInfo(BaseModel):
    id: str
    built_at: datetime
    content_hash: str
    n_markets: int | None
    n_bars: int | None
    n_prints: int | None
    coverage_start: datetime | None
    coverage_end: datetime | None


class CreateBacktestResponse(BaseModel):
    run_id: str
    status: RunStatus


class RunProgress(BaseModel):
    phase: str
    fraction: float
    updated_at: datetime | None = None


class RunSummary(BaseModel):
    id: str
    status: RunStatus
    strategy_name: str | None
    collection: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    execution_model: str | None
    net_profit: float | None
    roi: float | None
    n_trades: int | None
    signals_generated: int | None
    signals_filled: int | None
    warnings: list[str] = Field(default_factory=list)


class RunDetail(RunSummary):
    config: BacktestRequest
    config_hash: str
    engine_version: str
    dataset_version_id: str
    seed: int
    error: str | None
    progress: RunProgress | None
    metrics: dict[str, Any] | None
    data_quality: dict[str, Any] | None
    manifest: dict[str, Any] | None


class RunList(BaseModel):
    runs: list[RunSummary]


class EquityPoint(BaseModel):
    ts: datetime
    equity: str
    cash: str
    positions_value: str
    open_positions: int
    drawdown_pct: float = Field(
        description="Fall from the running peak of the full-resolution curve, computed server-side"
    )


class EquitySeries(BaseModel):
    run_id: str
    total_points: int
    returned_points: int
    downsampled: bool
    points: list[EquityPoint]


class TradeRow(BaseModel):
    seq: int
    fill_status: str
    condition_id: str
    clob_token_id: str
    side: str
    signal_ts: datetime
    entry_ts: datetime
    entry_ref_price: str | None
    entry_price: str | None
    shares: str
    notional: str
    entry_fee: str
    exit_ts: datetime | None
    exit_price: str | None
    exit_fee: str | None
    exit_reason: str | None
    gross_pnl: str | None
    fees_total: str | None
    slippage_cost: str | None
    net_pnl: str | None
    return_pct: str | None
    holding_seconds: int | None
    attempts: int | None
    refusal_reason: str | None
    question: str | None


class TradeDetail(TradeRow):
    entry_rule_trace: dict[str, Any]
    exit_rule_trace: dict[str, Any] | None


class TradePage(BaseModel):
    run_id: str
    trades: list[TradeRow]
    next_cursor: int | None
    total: int
    counts_by_status: dict[str, int]
