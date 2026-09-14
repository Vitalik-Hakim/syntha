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
from syntha_engine.strategy.view import MarketView, PositionView

RANDOM_DRAW = "random_draw"  # engine-supplied uniform draw for the bar (seeded)

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
class ExitDecision:
    reason: ExitReason
    rule: dict[str, Any] = field(default_factory=dict)


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

    # ------------------------------------------------------------ vectorized path

    def entry_mask(
        self, features: Mapping[str, FloatArray], random_draw: FloatArray | None
    ) -> tuple[np.ndarray, np.ndarray]:
        """(entry signal, some clause was null) per bar, before universe/constraint gating."""
        entry = self.dsl.entry
        if entry.random is not None:
            assert random_draw is not None
            enter = random_draw < entry.random.probability_per_bar
            return enter, np.zeros(enter.shape, dtype=bool)
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

    def evaluate_exit(self, view: MarketView, position: PositionView) -> ExitDecision | None:
        price = view.price
        for rule in self.dsl.exit.any:
            if rule.type == "TAKE_PROFIT_PCT":
                target = position.entry_price * (1 + rule.value)
                if price is not None and price >= target:
                    return ExitDecision(
                        ExitReason.TAKE_PROFIT,
                        {"type": rule.type, "target": target, "price": price},
                    )
            elif rule.type == "STOP_LOSS_PCT":
                target = position.entry_price * (1 - rule.value)
                if price is not None and price <= target:
                    return ExitDecision(
                        ExitReason.STOP_LOSS, {"type": rule.type, "target": target, "price": price}
                    )
            elif rule.type == "PRICE_ABOVE":
                if price is not None and price >= rule.value:
                    return ExitDecision(
                        ExitReason.PRICE_TARGET,
                        {"type": rule.type, "target": rule.value, "price": price},
                    )
            elif rule.type == "PRICE_BELOW":
                if price is not None and price <= rule.value:
                    return ExitDecision(
                        ExitReason.PRICE_TARGET,
                        {"type": rule.type, "target": rule.value, "price": price},
                    )
            elif rule.type == "MAX_HOLD_MINUTES":
                if position.holding_minutes >= rule.value:
                    return ExitDecision(
                        ExitReason.MAX_HOLD,
                        {
                            "type": rule.type,
                            "limit": rule.value,
                            "holding_minutes": position.holding_minutes,
                        },
                    )
            elif rule.type == "RULE":
                clause = _clause(rule.condition, view.features)
                if clause["passed"]:
                    return ExitDecision(ExitReason.RULE_EXIT, {"type": rule.type, "clause": clause})
        return None


def compile_strategy(dsl: StrategyDSL, step_minutes: int) -> DSLStrategy:
    """Validate and compile; raises FeatureError for unknown features or bad parameters."""
    return DSLStrategy(dsl, step_minutes)
