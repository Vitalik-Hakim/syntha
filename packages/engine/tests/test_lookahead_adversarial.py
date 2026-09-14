"""THE PRODUCT'S CONSCIENCE (PRD §17.4). DO NOT DELETE, SKIP OR WEAKEN THESE TESTS TO MAKE CI GREEN.

A backtester that lets a strategy see the future manufactures false confidence about real money.
Each test below is a "clairvoyant" strategy attempting one form of leakage through the only
interface strategies get (MarketView), run end to end through the engine. Every attempt must
raise. If one stops raising, the engine has a look-ahead hole: fix the engine, not the test.
"""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from syntha_contracts.enums import ExitReason
from syntha_engine.runner import run
from syntha_engine.strategy.compiler import EntryDecision, ExitDecision, FeatureRef
from syntha_engine.strategy.view import LookAheadError, MarketView, PositionView
from syntha_worker.fixtures import load_fixture

FIXTURE = Path(__file__).parent / "golden" / "fee_schedule_change"  # 10 bars of one market


class Clairvoyant:
    """A strategy whose entry evaluation performs one attack."""

    name = "clairvoyant"
    uses_random = False

    def __init__(self, attack: Callable[[MarketView], Any], lookback_bars: int = 3) -> None:
        self.attack = attack
        self.lookback_bars = lookback_bars
        self.feature_refs = (FeatureRef("price", ()),)

    def evaluate_entry(self, view: MarketView) -> EntryDecision:
        self.attack(view)
        return EntryDecision(False, [])

    def evaluate_exit(self, view: MarketView, position: PositionView) -> ExitDecision | None:
        return None


def _run_with(attack: Callable[[MarketView], Any]) -> None:
    fx = load_fixture(FIXTURE)
    run(fx.request, fx.dataset, fx.settlement, strategy=Clairvoyant(attack))


def test_reading_the_next_bar_raises() -> None:
    with pytest.raises(LookAheadError):
        _run_with(lambda v: v.bars[+1])


def test_index_zero_is_not_a_backdoor_to_the_oldest_or_next_bar() -> None:
    with pytest.raises(LookAheadError):
        _run_with(lambda v: v.bars[0])


def test_slicing_the_bar_window_raises() -> None:
    with pytest.raises(TypeError):
        _run_with(lambda v: v.bars[-3:])


def test_iterating_the_bar_window_raises() -> None:
    with pytest.raises(TypeError):
        _run_with(lambda v: list(v.bars))


def test_reading_market_resolution_raises() -> None:
    with pytest.raises(AttributeError):
        _run_with(lambda v: v.market.resolution)  # type: ignore[attr-defined]


def test_reading_resolved_at_raises() -> None:
    with pytest.raises(AttributeError):
        _run_with(lambda v: v.market.resolved_at)  # type: ignore[attr-defined]


def test_reading_final_volume_raises() -> None:
    with pytest.raises(AttributeError):
        _run_with(lambda v: v.market.final_volume)  # type: ignore[attr-defined]


def test_reading_actual_end_date_raises() -> None:
    with pytest.raises(AttributeError):
        _run_with(lambda v: v.market.end_date_actual)  # type: ignore[attr-defined]


def test_reading_a_future_bar_via_the_features_mapping_raises() -> None:
    with pytest.raises(KeyError):
        _run_with(lambda v: v.features["price(lead=1)"])


def test_undeclared_features_are_not_available() -> None:
    with pytest.raises(KeyError):
        _run_with(lambda v: v.features["traded_volume(lookback_minutes=5)"])


def test_view_objects_cannot_be_mutated_to_smuggle_data() -> None:
    def attack(v: MarketView) -> None:
        v.market.category = "leak"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        _run_with(attack)

    with pytest.raises(AttributeError):
        _run_with(lambda v: setattr(v.bars, "_bars", ()))


def test_market_static_has_no_restricted_fields_at_all() -> None:
    from syntha_engine.strategy.view import MarketStatic

    fields = set(MarketStatic.__dataclass_fields__)
    assert fields.isdisjoint(
        {"resolution", "resolved_at", "final_volume", "end_date_actual", "closed_time"}
    )
    instance = MarketStatic("c", "t", 0, "Yes", None, 0.01, 5.0, None)
    assert not hasattr(instance, "__dict__"), (
        "a __dict__ would let code attach arbitrary attributes"
    )


class Recorder:
    """Records everything a strategy can see, to prove none of it is from the future."""

    name = "recorder"
    uses_random = False
    lookback_bars = 5
    feature_refs = (
        FeatureRef("price", ()),
        FeatureRef("price_change_pct", (("lookback_minutes", 2),)),
        FeatureRef("traded_volume", (("lookback_minutes", 3),)),
    )

    def __init__(self) -> None:
        self.seen: list[tuple[Any, ...]] = []

    def evaluate_entry(self, view: MarketView) -> EntryDecision:
        oldest = -len(view.bars)
        bar_ts = [view.bars[i].ts for i in range(oldest, 0)]
        self.seen.append((view.now, max(bar_ts), dict(view.features), view.bars[-1].price))
        return EntryDecision(False, [])

    def evaluate_exit(self, view: MarketView, position: PositionView) -> ExitDecision | None:
        return ExitDecision(ExitReason.MAX_HOLD)


def test_no_view_ever_contains_a_bar_after_now_and_values_match_truncated_history() -> None:
    fx = load_fixture(FIXTURE)
    full = Recorder()
    run(fx.request, fx.dataset, fx.settlement, strategy=full)
    assert full.seen
    for now, newest_bar, _, _ in full.seen:
        assert newest_bar <= now

    # What the strategy saw at each bar must equal what it would see if the future didn't exist.
    import numpy as np

    from syntha_engine.data import Dataset, TokenData

    for now, _, features, last_price in full.seen:
        cutoff = int(now.timestamp()) + 60  # the bar's close
        tokens = {}
        for tid, t in fx.dataset.tokens.items():
            b, p = t.bar_ts < cutoff, t.print_ts < cutoff
            tokens[tid] = TokenData(
                t.clob_token_id,
                t.condition_id,
                t.outcome_index,
                t.outcome_name,
                t.bar_ts[b],
                t.bar_price[b],
                t.print_ts[p],
                t.print_price[p],
                t.print_size[p],
                t.print_side[p],
            )
        truncated = Dataset(fx.dataset.markets, tokens, fx.dataset.fee_schedules)
        request = fx.request.model_copy(
            update={"dataset": fx.request.dataset.model_copy(update={"end": now})}
        )
        partial = Recorder()
        run(request, truncated, fx.settlement, strategy=partial)
        match = [s for s in partial.seen if s[0] == now]
        assert match, f"bar {now} not evaluated on truncated data"
        _, _, feats_trunc, price_trunc = match[0]
        assert price_trunc == last_price
        for key, value in features.items():
            other = feats_trunc[key]
            assert (value is None and other is None) or np.isclose(value, other), (
                now,
                key,
                value,
                other,
            )
