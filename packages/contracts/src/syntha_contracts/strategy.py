"""Strategy DSL (PRD §8). The visual builder is a view; this document is the artifact.

Versioned, hashable, diffable. Unknown fields and unknown feature names are rejected, never
ignored. Parameter validity per feature (e.g. which lookbacks are allowed) is checked by the
engine's compiler, which owns the feature registry.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from syntha_contracts.enums import Side, SizingMode

DSL_VERSION = 1

# The engine's feature registry must match this list exactly (tested).
FeatureName = Literal[
    "price",
    "price_change_pct",
    "price_change_abs",
    "traded_volume",
    "volume_ratio",
    "print_count",
    "vwap_gap",
    "realized_vol",
    "time_to_resolution_minutes",
    "time_since_open_minutes",
    "spread_proxy",
    "staleness_minutes",
    "minutes_since_last_print",
]

Op = Literal["lt", "lte", "gt", "gte", "eq", "neq"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Strict):
    feature: FeatureName
    params: dict[str, int] = Field(default_factory=dict)
    op: Op
    value: float


class Universe(_Strict):
    side: Side = Side.BUY_YES
    categories: list[str] | None = None
    min_volume_asof: float | None = Field(
        default=None, ge=0, description="Cumulative traded notional (USDC) up to the bar."
    )
    min_time_to_resolution_minutes: int | None = Field(
        default=None, ge=0, description="Minutes until the *scheduled* end date."
    )


class RandomEntry(_Strict):
    """Null-hypothesis entries (PRD §8.3): each eligible bar enters with this probability."""

    probability_per_bar: float = Field(gt=0, le=1)


class Entry(_Strict):
    all: list[Condition] | None = None
    any: list[Condition] | None = None
    random: RandomEntry | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Entry:
        given = [x for x in (self.all, self.any, self.random) if x is not None]
        if len(given) != 1:
            raise ValueError("entry needs exactly one of `all`, `any` or `random`")
        return self


class Sizing(_Strict):
    mode: SizingMode
    value: float = Field(gt=0)
    max_notional_per_trade: float | None = Field(default=None, gt=0)
    min_order_policy: Literal["SKIP", "ROUND_UP"] = Field(
        default="SKIP",
        description="When the sized order is below the market's minimum order size: SKIP rejects "
        "it (REJECTED_MIN_SIZE); ROUND_UP places the minimum instead, if cash, exposure headroom, "
        "max_notional_per_trade and the liquidity cap all still allow it and it is at most "
        "round_up_max_multiple times the requested notional.",
    )
    round_up_max_multiple: float = Field(default=2.0, ge=1.0, le=20.0)


class TakeProfit(_Strict):
    type: Literal["TAKE_PROFIT_PCT"]
    value: float = Field(gt=0)


class StopLoss(_Strict):
    type: Literal["STOP_LOSS_PCT"]
    value: float = Field(gt=0, lt=1)


class PriceAbove(_Strict):
    type: Literal["PRICE_ABOVE"]
    value: float = Field(gt=0, lt=1)


class PriceBelow(_Strict):
    type: Literal["PRICE_BELOW"]
    value: float = Field(gt=0, lt=1)


class MaxHold(_Strict):
    type: Literal["MAX_HOLD_MINUTES"]
    value: int = Field(gt=0)


class RuleExit(_Strict):
    type: Literal["RULE"]
    condition: Condition


ExitRule = Annotated[
    TakeProfit | StopLoss | PriceAbove | PriceBelow | MaxHold | RuleExit,
    Field(discriminator="type"),
]


class Exit(_Strict):
    """Evaluated in list order; the first rule that fires names the exit reason. An empty list
    means hold until settlement (or the end of the backtest)."""

    any: list[ExitRule] = Field(default_factory=list)


class Constraints(_Strict):
    max_open_positions: int = Field(default=5, ge=1)
    max_total_exposure_pct: float = Field(default=0.25, gt=0, le=1)
    max_positions_per_event: int = Field(default=1, ge=1)
    cooldown_minutes_per_market: int = Field(default=0, ge=0)
    allow_reentry_same_market: bool = True


class StrategyDSL(_Strict):
    dsl_version: Literal[1] = 1
    name: str = Field(min_length=1)
    description: str | None = None
    universe: Universe = Field(default_factory=Universe)
    entry: Entry
    sizing: Sizing
    exit: Exit = Field(default_factory=Exit)
    constraints: Constraints = Field(default_factory=Constraints)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def dsl_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def strategy_json_schema() -> dict[str, Any]:
    return StrategyDSL.model_json_schema()
