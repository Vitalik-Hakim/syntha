"""In-memory dataset handed to the engine (PRD §6). Built by a loader outside this package.

Split by who may see what (PRD §5.1 rule 4):
- `MarketTerms` / `TokenData`: pre-trade facts (order constraints, fee schedule, prices, prints).
- `SettlementTable`: outcome, resolution time and trading-close time. Only settlement and the
  engine's trading-halt checks receive it; the strategy evaluator never does.

Times are UTC epoch seconds (int). Prices and sizes are float64 arrays for vectorized features;
money is converted to Decimal at the execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

AppliesTo = Literal["ALL_OPEN_MARKETS", "MARKETS_CREATED_AFTER"]


@dataclass(frozen=True, slots=True)
class FeeScheduleEntry:
    fee_type: str
    rate: Decimal
    exponent: int
    applies_to: AppliesTo
    effective_from: int
    effective_to: int | None = None

    @property
    def label(self) -> str:
        return f"{self.fee_type}@{self.effective_from}"


@dataclass(frozen=True, slots=True)
class MarketTerms:
    condition_id: str
    event_id: str | None
    category: str | None
    tick_size: Decimal
    min_order_size: Decimal | None  # None = unknown upstream → any order is UNEXECUTABLE
    created_ts: int | None
    open_ts: int | None
    end_ts: int | None  # scheduled end, visible to strategies via time_to_resolution
    fees_enabled: bool | None
    fee_type: str | None
    fee_rate: Decimal | None
    fee_exponent: int | None
    # v3 §4.2: a catch-all sibling ("Other", "None of the above") makes an event's set exhaustive.
    # None = not known (no event data); the EventView reports it as not present.
    is_catch_all: bool | None = None
    # v3 E3: Polymarket's neg-risk flag. Only a neg-risk event's markets are one exhaustive,
    # mutually exclusive set; a basket over anything else is a directional bet, not a set.
    neg_risk: bool | None = None


@dataclass(frozen=True, slots=True)
class TokenData:
    clob_token_id: str
    condition_id: str
    outcome_index: int
    outcome_name: str
    bar_ts: IntArray  # sampled price minutes, ascending, unique
    bar_price: FloatArray
    print_ts: IntArray  # taker prints, ascending
    print_price: FloatArray
    print_size: FloatArray
    print_side: npt.NDArray[np.int8]  # +1 BUY, -1 SELL (taker side)
    prior_notional: float = 0.0  # traded notional before the first print supplied (as-of volume)
    # v3 E4: index into Dataset.wallets per print, -1 when unknown. Wallets are pseudonymous
    # public addresses; they never leave the engine's outputs except as ids in traces.
    print_wallet: npt.NDArray[np.int32] | None = None

    def __post_init__(self) -> None:
        n, m = len(self.bar_ts), len(self.print_ts)
        if len(self.bar_price) != n:
            raise ValueError(f"{self.clob_token_id}: bar arrays differ in length")
        if not (len(self.print_price) == len(self.print_size) == len(self.print_side) == m):
            raise ValueError(f"{self.clob_token_id}: print arrays differ in length")
        if self.print_wallet is not None and len(self.print_wallet) != m:
            raise ValueError(f"{self.clob_token_id}: print_wallet differs in length")
        if n > 1 and not bool(np.all(np.diff(self.bar_ts) > 0)):
            raise ValueError(f"{self.clob_token_id}: bar timestamps must be strictly ascending")
        if m > 1 and not bool(np.all(np.diff(self.print_ts) >= 0)):
            raise ValueError(f"{self.clob_token_id}: print timestamps must be ascending")


Resolution = str  # YES | NO | INVALID | UNRESOLVED | OUTCOME_<n>


@dataclass(frozen=True, slots=True)
class Settlement:
    resolution: Resolution
    resolved_ts: int | None
    closed_ts: int | None

    def payout(self, outcome_index: int) -> Decimal | None:
        """Per-share settlement value for an outcome token, or None if not (yet) resolved."""
        r = self.resolution
        if r == "UNRESOLVED" or self.resolved_ts is None:
            return None
        if r == "INVALID":
            return Decimal("0.5")
        if r == "YES":
            winner = 0
        elif r == "NO":
            winner = 1
        elif r.startswith("OUTCOME_"):
            winner = int(r.removeprefix("OUTCOME_"))
        else:
            raise ValueError(f"unknown resolution {r!r}")
        return Decimal(1) if outcome_index == winner else Decimal(0)


@dataclass(frozen=True, slots=True)
class SettlementTable:
    by_condition: dict[str, Settlement] = field(default_factory=dict)

    def get(self, condition_id: str) -> Settlement:
        return self.by_condition.get(condition_id, Settlement("UNRESOLVED", None, None))


@dataclass(frozen=True, slots=True)
class Dataset:
    markets: dict[str, MarketTerms]
    tokens: dict[str, TokenData]
    fee_schedules: tuple[FeeScheduleEntry, ...] = ()
    dataset_version_id: str | None = None
    # How the markets were selected (collection manifest definition), used for bias warnings.
    selection: dict[str, Any] | None = None
    # v3 E4: wallet addresses by id, for TokenData.print_wallet.
    wallets: tuple[str, ...] = ()
    # The collection's coverage window (epoch seconds): the period its markets were scheduled to
    # end in. Capacity per month is measured over it (M9 decision, docs/DECISIONS.md).
    coverage: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        for tok in self.tokens.values():
            if tok.condition_id not in self.markets:
                raise ValueError(f"token {tok.clob_token_id} references unknown market")
