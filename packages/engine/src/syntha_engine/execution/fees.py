"""Taker fees (PRD §3.7, corrected by docs/DATA_NOTES.md "Fees").

fee = shares × rate × (p × (1 − p))^exponent, rounded to 5 decimal places (half-even).
Makers never pay; v1 models the user as a taker only.

Fee modes:
- HISTORICAL: the market's schedule applies only from its fee_type's effective date (and, for
  MARKETS_CREATED_AFTER types, only to markets created after it); zero before.
- CURRENT: the market's current schedule throughout ("would this work now?").
- FIXED: a user-supplied rate for every fill; a counterfactual, labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from syntha_contracts.backtest import FeeConfig
from syntha_contracts.enums import FeeMode
from syntha_engine.data import FeeScheduleEntry, MarketTerms

FEE_QUANTUM = Decimal("0.00001")
ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class FeeTerms:
    rate: Decimal
    exponent: int
    label: str | None  # which schedule applied; None when no fee applies
    warning: str | None = None


NO_FEE = FeeTerms(ZERO, 1, None)


def resolve_fee_terms(
    terms: MarketTerms, fill_ts: int, config: FeeConfig, schedules: tuple[FeeScheduleEntry, ...]
) -> FeeTerms:
    if config.mode == FeeMode.FIXED:
        assert config.fixed_rate is not None
        return FeeTerms(config.fixed_rate, config.fixed_exponent, "FIXED")
    if not terms.fees_enabled or not terms.fee_type or terms.fee_rate is None:
        return NO_FEE
    exponent = terms.fee_exponent or 1
    if config.mode == FeeMode.CURRENT:
        return FeeTerms(terms.fee_rate, exponent, f"{terms.fee_type}@current")

    candidates = [s for s in schedules if s.fee_type == terms.fee_type]
    if not candidates:
        # Unknown rollout date: charging the market's rate is the conservative choice.
        return FeeTerms(
            terms.fee_rate,
            exponent,
            f"{terms.fee_type}@unscheduled",
            warning="fee_type_without_schedule",
        )
    for s in sorted(candidates, key=lambda s: s.effective_from, reverse=True):
        if fill_ts < s.effective_from or (s.effective_to is not None and fill_ts >= s.effective_to):
            continue
        if s.applies_to == "MARKETS_CREATED_AFTER" and (
            terms.created_ts is None or terms.created_ts < s.effective_from
        ):
            continue
        return FeeTerms(terms.fee_rate, exponent, s.label)
    return NO_FEE


def fee_amount(shares: Decimal, price: Decimal, fee: FeeTerms) -> Decimal:
    if fee.rate == ZERO or shares == ZERO:
        return ZERO
    raw = shares * fee.rate * (price * (1 - price)) ** fee.exponent
    return raw.quantize(FEE_QUANTUM, rounding=ROUND_HALF_EVEN)
