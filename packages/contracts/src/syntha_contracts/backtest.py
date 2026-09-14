"""BacktestRequest: the single, hashable description of a run (PRD §14).

Same (config_hash, dataset_version, seed, engine_version) → byte-identical results (PRD §17.3).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from syntha_contracts.enums import ExecutionModel, FeeMode, PartialFillPolicy
from syntha_contracts.strategy import StrategyDSL

BarInterval = Literal[1, 5, 15, 60]
Preset = Literal["OPTIMISTIC", "REALISTIC", "CONSERVATIVE"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionConfig(_Strict):
    model: ExecutionModel = ExecutionModel.TRADE_PRINT
    slippage_ticks: int | None = Field(default=1, ge=0)
    slippage_bps: float | None = Field(default=None, ge=0)
    liquidity_participation: float | None = Field(
        default=0.10,
        gt=0,
        le=1,
        description="Share of the bar's printed size we may take. None = unlimited.",
    )
    partial_fill_policy: PartialFillPolicy = PartialFillPolicy.ALLOW
    allow_fill_on_no_prints: bool = False
    adverse_selection: bool = Field(
        default=False,
        description="TRADE_PRINT fills at the worst print in the bar instead of VWAP.",
    )

    @model_validator(mode="after")
    def _one_slippage_unit(self) -> ExecutionConfig:
        if (self.slippage_ticks is None) == (self.slippage_bps is None):
            raise ValueError("set exactly one of slippage_ticks or slippage_bps")
        if self.model == ExecutionModel.BOOK:
            raise ValueError(
                "BOOK execution requires recorded L2 data and is not available in v1 (PRD §11.4)"
            )
        return self

    @classmethod
    def preset(cls, name: Preset) -> ExecutionConfig:
        """PRD §11.6. Fees are part of the preset too; see FeeConfig.preset."""
        if name == "OPTIMISTIC":
            return cls(
                model=ExecutionModel.LAST_PRICE,
                slippage_ticks=0,
                liquidity_participation=None,
                allow_fill_on_no_prints=True,
            )
        if name == "REALISTIC":
            return cls(
                model=ExecutionModel.TRADE_PRINT,
                slippage_ticks=1,
                liquidity_participation=0.10,
                partial_fill_policy=PartialFillPolicy.ALLOW,
            )
        return cls(
            model=ExecutionModel.TRADE_PRINT,
            slippage_ticks=2,
            adverse_selection=True,
            liquidity_participation=0.05,
            partial_fill_policy=PartialFillPolicy.REJECT,
        )


class FeeConfig(_Strict):
    mode: FeeMode = FeeMode.HISTORICAL
    fixed_rate: Decimal | None = Field(default=None, ge=0)
    fixed_exponent: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _fixed_needs_rate(self) -> FeeConfig:
        if self.mode == FeeMode.FIXED and self.fixed_rate is None:
            raise ValueError(
                "FIXED fee mode needs fixed_rate (use 0 for a fee-free counterfactual)"
            )
        return self

    @classmethod
    def preset(cls, name: Preset) -> FeeConfig:
        return cls(mode=FeeMode.CURRENT if name == "CONSERVATIVE" else FeeMode.HISTORICAL)


class DatasetSelection(_Strict):
    collection: str | None = Field(default=None, description="Collection name; None = all markets.")
    start: datetime | None = None
    end: datetime | None = None
    condition_ids: list[str] | None = None


class BacktestRequest(_Strict):
    strategy: StrategyDSL
    dataset: DatasetSelection = Field(default_factory=DatasetSelection)
    starting_capital: Decimal = Field(default=Decimal("100"), gt=0)
    seed: int = 0
    bar_interval_minutes: BarInterval = 1
    decision_lag_bars: int = Field(default=1, ge=0)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    fees: FeeConfig = Field(default_factory=FeeConfig)
    share_precision: int = Field(default=2, ge=0, le=6)
    max_stale_minutes: int = Field(
        default=60, ge=0, description="Entries need a price observed within this many minutes."
    )
    settle_winnings_fee_free: bool = Field(
        default=True, description="No fee on redemption (docs/DATA_NOTES.md assumptions)."
    )
    check_invariants: bool = True

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()
