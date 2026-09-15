"""Strategy protocol and the DSL compiler (PRD §8, §13.1).

Two evaluation paths, which must agree:
- `entry_mask`: vectorized over a token's bars, used to find candidate bars quickly (PRD §9.2).
- `evaluate_entry(view)`: scalar, through the look-ahead-safe MarketView. It runs at every
  candidate bar to produce the rule trace, and the runner fails the run if it disagrees with
  the mask.

Custom Python strategies implement only the scalar methods and are evaluated bar by bar.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from syntha_contracts.enums import ExitReason
from syntha_contracts.strategy import Condition, StrategyDSL
from syntha_engine.data import FloatArray
from syntha_engine.strategy import features as F
from syntha_engine.strategy.view import EventView, MarketView, PositionView

RANDOM_DRAW = "random_draw"  # engine-supplied uniform draw for the bar (seeded)
PRINT_TRIGGER = (
    "print_trigger"  # engine-supplied notional of the qualifying print in the bar (v3 E4)
)

_OPS: dict[str, Callable[[Any, Any], Any]] = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
    "eq": operator.eq,
    "neq": operator.ne,
}


@dataclass(frozen=True, slots=True)
class FeatureRef:
    name: str
    params: tuple[tuple[str, int], ...]

    @property
    def key(self) -> str:
        return F.feature_key(self.name, dict(self.params))

    @classmethod
    def of(cls, cond: Condition) -> FeatureRef:
        return cls(cond.feature, tuple(sorted(cond.params.items())))


@dataclass(frozen=True, slots=True)
class EntryDecision:
    enter: bool
    clauses: list[dict[str, Any]]
    null_feature: bool = False


@dataclass(frozen=True, slots=True)
class BasketDecision:
    """v3 E3: buy one unit of each leg (token id → outcome index) as one all-or-nothing order."""

    legs: tuple[str, ...]
    kind: str
    cost_per_unit: float  # Σ leg prices at the decision bar
    payoff_per_unit: float  # 1 for a YES set, N − 1 for a NO set
    trace: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExitDecision:
    reason: ExitReason
    rule: dict[str, Any] = field(default_factory=dict)
    # Every exit rule evaluated at the bar, in list order (PRD §13.1): the checklist behind `rule`.
    checks: list[dict[str, Any]] = field(default_factory=list)


class Strategy(Protocol):
    name: str
    lookback_bars: int
    feature_refs: tuple[FeatureRef, ...]
    uses_random: bool

    def evaluate_entry(self, view: MarketView) -> EntryDecision: ...

    def evaluate_exit(self, view: MarketView, position: PositionView) -> ExitDecision | None: ...


def _clause(cond: Condition, features: Mapping[str, float | None]) -> dict[str, Any]:
    key = F.feature_key(cond.feature, cond.params)
    actual = features[key]
    null = actual is None
    passed = (not null) and bool(_OPS[cond.op](actual, cond.value))
    trace: dict[str, Any] = {
        "feature": cond.feature,
        "op": cond.op,
        "threshold": cond.value,
        "actual": actual,
        "passed": passed,
    }
    if cond.params:
        trace["params"] = dict(cond.params)
    if null:
        trace["null"] = True
    return trace


class DSLStrategy:
    """A compiled StrategyDSL document."""

    def __init__(self, dsl: StrategyDSL, step_minutes: int) -> None:
        self.dsl = dsl
        self.name = dsl.name
        conditions = list(dsl.entry.all or dsl.entry.any or [])
        conditions += [r.condition for r in dsl.exit.any if r.type == "RULE"]
        lookback = 1
        refs: dict[str, FeatureRef] = {}
        for cond in conditions:
            lookback = max(lookback, F.validate(cond.feature, cond.params, step_minutes) + 1)
            ref = FeatureRef.of(cond)
            refs[ref.key] = ref
        self.feature_refs = tuple(refs[k] for k in sorted(refs))
        self.lookback_bars = lookback
        self.uses_random = dsl.entry.random is not None
        self.uses_print_trigger = dsl.entry.print_trigger is not None
        self.uses_basket = dsl.entry.basket is not None

    # ------------------------------------------------------------ vectorized path

    def entry_mask(
        self, features: Mapping[str, FloatArray], random_draw: FloatArray | None, n: int = 0
    ) -> tuple[np.ndarray, np.ndarray]:
        """(entry signal, some clause was null) per bar, before universe/constraint gating. `n`
        is the bar count when the strategy declares no features (baskets)."""
        entry = self.dsl.entry
        if entry.random is not None:
            assert random_draw is not None
            enter = random_draw < entry.random.probability_per_bar
            return enter, np.zeros(enter.shape, dtype=bool)
        if entry.print_trigger is not None:
            trig = features[PRINT_TRIGGER]  # notional of the qualifying print, NaN when none
            enter = ~np.isnan(trig)
            return enter, np.zeros(enter.shape, dtype=bool)
        if entry.basket is not None:
            n = next(iter(features.values())).size if features else n
            return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)  # baskets fire on EventView
        conds = entry.all if entry.all is not None else entry.any
        assert conds is not None
        n = next(iter(features.values())).size if features else 0
        results, nulls = [], np.zeros(n, dtype=bool)
        for cond in conds:
            values = features[F.feature_key(cond.feature, cond.params)]
            is_null = np.isnan(values)
            with np.errstate(invalid="ignore"):
                results.append(_OPS[cond.op](values, cond.value) & ~is_null)
            nulls |= is_null
        if not results:
            return np.ones(n, dtype=bool), nulls
        combine = np.logical_and if entry.all is not None else np.logical_or
        return combine.reduce(results), nulls

    # ------------------------------------------------------------ scalar path

    def evaluate_entry(self, view: MarketView) -> EntryDecision:
        entry = self.dsl.entry
        if entry.print_trigger is not None:
            notional = view.features[PRINT_TRIGGER]
            t = entry.print_trigger
            return EntryDecision(
                notional is not None,
                [
                    {
                        "feature": PRINT_TRIGGER,
                        "op": "gte",
                        "threshold": t.min_notional,
                        "actual": notional,
                        "passed": notional is not None,
                        "params": {
                            "side": t.side,
                            "wallets": len(t.wallets) if t.wallets else None,
                        },
                    }
                ],
            )
        if entry.basket is not None:
            return EntryDecision(False, [])  # baskets are decided by evaluate_event
        if entry.random is not None:
            draw = view.features[RANDOM_DRAW]
            p = entry.random.probability_per_bar
            passed = draw is not None and draw < p
            return EntryDecision(
                bool(passed),
                [
                    {
                        "feature": RANDOM_DRAW,
                        "op": "lt",
                        "threshold": p,
                        "actual": draw,
                        "passed": bool(passed),
                    }
                ],
            )
        conds = entry.all if entry.all is not None else entry.any
        assert conds is not None
        clauses = [_clause(c, view.features) for c in conds]
        passes = [c["passed"] for c in clauses]
        enter = (all(passes) if entry.all is not None else any(passes)) if clauses else True
        return EntryDecision(enter, clauses, any(c.get("null") for c in clauses))

    @staticmethod
    def _fires(rule: Any, price: float | None, view: MarketView, position: PositionView) -> bool:
        """Whether one exit rule holds. The single definition used by both the hot path and the
        trace."""
        kind = rule.type
        if kind == "TAKE_PROFIT_PCT":
            return price is not None and price >= position.entry_price * (1 + rule.value)
        if kind == "STOP_LOSS_PCT":
            return price is not None and price <= position.entry_price * (1 - rule.value)
        if kind == "PRICE_ABOVE":
            return price is not None and price >= rule.value
        if kind == "PRICE_BELOW":
            return price is not None and price <= rule.value
        if kind == "MAX_HOLD_MINUTES":
            return bool(position.holding_minutes >= rule.value)
        if kind == "WALLET_SELL":
            return view.wallet_sold
        cond = rule.condition
        actual = view.features[F.feature_key(cond.feature, cond.params)]
        return actual is not None and bool(_OPS[cond.op](actual, cond.value))

    def _exit_check(
        self, rule: Any, price: float | None, view: MarketView, position: PositionView
    ) -> dict[str, Any]:
        passed = self._fires(rule, price, view, position)
        if rule.type in ("TAKE_PROFIT_PCT", "STOP_LOSS_PCT"):
            up = rule.type == "TAKE_PROFIT_PCT"
            return {
                "type": rule.type,
                "value": rule.value,
                "target": position.entry_price * (1 + rule.value if up else 1 - rule.value),
                "price": price,
                "passed": passed,
            }
        if rule.type in ("PRICE_ABOVE", "PRICE_BELOW"):
            return {"type": rule.type, "target": rule.value, "price": price, "passed": passed}
        if rule.type == "MAX_HOLD_MINUTES":
            return {
                "type": rule.type,
                "limit": rule.value,
                "holding_minutes": position.holding_minutes,
                "passed": passed,
            }
        if rule.type == "WALLET_SELL":
            return {
                "type": rule.type,
                "signal_wallet": position.signal_wallet,
                "wallet_sold_this_bar": view.wallet_sold,
                "passed": passed,
            }
        clause = _clause(rule.condition, view.features)
        assert clause["passed"] == passed
        return {"type": rule.type, "clause": clause, "passed": passed}

    def evaluate_exit(self, view: MarketView, position: PositionView) -> ExitDecision | None:
        """The first rule (in list order) that holds names the exit. Only when one fires is every
        rule evaluated again for the trace."""
        price = view.price
        rules = self.dsl.exit.any
        for rule in rules:
            if self._fires(rule, price, view, position):
                checks = [self._exit_check(r, price, view, position) for r in rules]
                first = next(i for i, c in enumerate(checks) if c["passed"])
                checks[first]["fired"] = True
                return ExitDecision(_EXIT_REASONS[rule.type], checks[first], checks)
        return None

    def exit_plan(self, entry_price: float) -> list[dict[str, Any]]:
        """The exit rules resolved against a fill: price levels for price-based exits, the holding
        limit for time exits. Uses the same arithmetic as `evaluate_exit`."""
        plan: list[dict[str, Any]] = []
        for rule in self.dsl.exit.any:
            if rule.type == "TAKE_PROFIT_PCT":
                plan.append(
                    {
                        "type": rule.type,
                        "value": rule.value,
                        "level": entry_price * (1 + rule.value),
                    }
                )
            elif rule.type == "STOP_LOSS_PCT":
                plan.append(
                    {
                        "type": rule.type,
                        "value": rule.value,
                        "level": entry_price * (1 - rule.value),
                    }
                )
            elif rule.type == "MAX_HOLD_MINUTES":
                plan.append({"type": rule.type, "limit_minutes": rule.value})
            elif rule.type == "WALLET_SELL":
                plan.append({"type": rule.type, "wallet_ref": rule.wallet_ref})
            elif rule.type == "RULE":
                c = rule.condition
                plan.append(
                    {
                        "type": rule.type,
                        "clause": {
                            "feature": c.feature,
                            "params": dict(c.params),
                            "op": c.op,
                            "threshold": c.value,
                        },
                    }
                )
            else:  # PRICE_ABOVE / PRICE_BELOW
                plan.append({"type": rule.type, "level": rule.value})
        return plan

    # ------------------------------------------------------------ near misses (PRD §13.2)

    def entry_closeness(self, features: Mapping[str, FloatArray]) -> FloatArray | None:
        """How far each bar is from firing; lower is nearer, 0 when the entry fires.

        For `all`, the integer part is the number of failing clauses and the fraction their mean
        distance; for `any`, 1 plus half the smallest distance (one clause has to flip). A failing
        clause's distance is |actual − threshold| / |threshold| (or / 1 for a zero threshold),
        capped at 1, and 1 for a null feature. None for random entries, which have no near misses.
        """
        entry = self.dsl.entry
        conds = entry.all if entry.all is not None else entry.any
        if entry.random is not None or not conds:
            return None
        n = next(iter(features.values())).size
        failed = np.zeros(n)
        dist = np.zeros(n) if entry.all is not None else np.ones(n)
        for cond in conds:
            values = features[F.feature_key(cond.feature, cond.params)]
            null = np.isnan(values)
            with np.errstate(invalid="ignore", over="ignore"):
                ok = _OPS[cond.op](values, cond.value) & ~null
                d = np.abs(values - cond.value)
                d /= abs(cond.value) or 1.0
            np.clip(d, 1e-9, 1.0, out=d)
            d[null] = 1.0
            d[ok] = 0.0
            failed += ~ok
            if entry.all is not None:
                dist += d
            else:
                np.minimum(dist, d, out=dist)
        if entry.all is not None:
            score: FloatArray = failed + dist / (len(conds) + 1)
        else:
            score = np.where(failed == len(conds), 1.0 + dist / 2, 0.0)
        return score

    # ------------------------------------------------------------ baskets (v3 E3)

    def evaluate_event(self, view: EventView, fee_per_unit: float) -> BasketDecision | None:
        """A YES set is bought when Σ P(yes) ≤ 1 − c, a NO set when Σ P(no) ≤ (N − 1) − c, with
        c = fees on one unit + one tick per leg + min_edge. Sets without a listed catch-all are
        not exhaustive and are skipped unless the strategy says otherwise (v3 §4.2)."""
        basket = self.dsl.entry.basket
        if basket is None or not view.neg_risk:
            return None
        legs = [s for s in view.siblings if s.price is not None]
        if len(legs) < basket.min_markets or len(legs) != view.n_markets_open:
            return None
        if basket.require_catch_all and not view.catch_all_present:
            return None
        cost = sum(s.price for s in legs if s.price is not None)
        ticks = sum(s.tick_size for s in legs)
        payoff = 1.0 if basket.kind == "NEGRISK_YES_SET" else float(len(legs) - 1)
        margin = payoff - cost - fee_per_unit - ticks - basket.min_edge
        trace = {
            "kind": basket.kind,
            "n_legs": len(legs),
            "cost_per_unit": cost,
            "payoff_per_unit": payoff,
            "fee_per_unit": fee_per_unit,
            "ticks_per_unit": ticks,
            "min_edge": basket.min_edge,
            "margin": margin,
            "catch_all_present": view.catch_all_present,
            "passed": margin >= 0,
        }
        if margin < 0:
            return None
        return BasketDecision(
            tuple(s.clob_token_id for s in legs), basket.kind, cost, payoff, trace
        )


_EXIT_REASONS = {
    "WALLET_SELL": ExitReason.WALLET_SELL,
    "TAKE_PROFIT_PCT": ExitReason.TAKE_PROFIT,
    "STOP_LOSS_PCT": ExitReason.STOP_LOSS,
    "PRICE_ABOVE": ExitReason.PRICE_TARGET,
    "PRICE_BELOW": ExitReason.PRICE_TARGET,
    "MAX_HOLD_MINUTES": ExitReason.MAX_HOLD,
    "RULE": ExitReason.RULE_EXIT,
}


def compile_strategy(dsl: StrategyDSL, step_minutes: int) -> DSLStrategy:
    """Validate and compile; raises FeatureError for unknown features or bad parameters."""
    return DSLStrategy(dsl, step_minutes)
