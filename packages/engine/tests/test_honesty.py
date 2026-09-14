"""The honesty layer (PRD §15): bootstrap intervals, the permutation test, the random-entry baseline
and buy-and-hold. The null models must be able to find a real edge, and must reproduce the real
run when handed the strategy's own signals."""

from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.builtins import builtin_strategy
from syntha_engine import honesty as H
from syntha_engine.data import Dataset, MarketTerms, Settlement, SettlementTable, TokenData
from syntha_engine.runner import BacktestResult, LeanOutcome, prepare, simulate

T0 = 1_767_225_600


def _market(cid: str) -> MarketTerms:
    return MarketTerms(
        cid,
        f"e-{cid}",
        "test",
        Decimal("0.01"),
        Decimal("1"),
        T0,
        T0,
        None,
        False,
        None,
        None,
        None,
    )


def _token(cid: str, prices: list[float]) -> TokenData:
    n = len(prices)
    empty_i, empty_f = np.empty(0, np.int64), np.empty(0)
    return TokenData(
        f"{cid}-yes",
        cid,
        0,
        "Yes",
        T0 + np.arange(n, dtype=np.int64) * 60,
        np.array(prices),
        empty_i,
        empty_f,
        empty_f,
        np.empty(0, np.int8),
    )


def _planted(blocks: int = 5) -> tuple[Dataset, SettlementTable]:
    """Market A jumps (the signal) and keeps rising; market B is flat. Picking A is the edge."""
    a: list[float] = []
    for _ in range(blocks):
        a += [0.50] * 10 + [0.55, 0.55] + [0.70] * 8
    b = [0.50] * len(a)
    markets = {"a": _market("a"), "b": _market("b")}
    tokens = {"a-yes": _token("a", a), "b-yes": _token("b", b)}
    return Dataset(markets, tokens), SettlementTable(
        {cid: Settlement("UNRESOLVED", None, None) for cid in markets}
    )


def _request(**honesty: Any) -> BacktestRequest:
    return BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "planted",
                "entry": {
                    "all": [
                        {
                            "feature": "price_change_pct",
                            "params": {"lookback_minutes": 1},
                            "op": "gte",
                            "value": 0.05,
                        }
                    ]
                },
                "sizing": {"mode": "FIXED_CASH", "value": 10},
                "exit": {
                    "any": [
                        {"type": "TAKE_PROFIT_PCT", "value": 0.1},
                        {"type": "MAX_HOLD_MINUTES", "value": 5},
                    ]
                },
                "constraints": {"max_open_positions": 5, "max_positions_per_event": 1},
            },
            "starting_capital": "100",
            "seed": 7,
            "execution": {
                "model": "LAST_PRICE",
                "slippage_ticks": 0,
                "liquidity_participation": None,
            },
            "honesty": {"permutations": 200, "random_baselines": 40, **honesty},
        }
    )


def _run(request: BacktestRequest) -> tuple[Any, BacktestResult, dict[str, Any]]:
    ds, st = _planted()
    prep = prepare(request, ds, st)
    result = simulate(prep)
    assert isinstance(result, BacktestResult)
    h = H.compute_honesty(
        prep, result, buy_and_hold_dsl=builtin_strategy("buy_and_hold_to_resolution")
    )
    return prep, result, h


def test_replaying_the_real_signals_reproduces_the_real_run() -> None:
    prep, result, _ = _run(_request(permutations=0, random_baselines=0))
    assert result.metrics["n_trades"] == 5 and result.metrics["roi"] > 0
    episodes = H._real_episodes(prep, result)
    replay = simulate(prep, forced=H._schedule(prep, episodes, "REPLAY"))
    assert isinstance(replay, LeanOutcome)
    assert replay.roi == result.metrics["roi"]
    assert replay.signals_filled == result.data_quality["signals_filled"]


def test_permutation_test_finds_a_planted_edge() -> None:
    _, _result, h = _run(_request())
    perm = h["permutation"]
    assert perm["iterations"] == 200 and perm["episodes"] == 5
    # a variant only ties the strategy when every episode happens to stay in market A (1 in 32)
    assert perm["percentile"] >= 95
    assert h["summary"]["evidence_of_edge"] is True
    assert "At or above 95%" in h["summary"]["reading"]
    assert h["random_baseline"]["percentile"] >= 90


def test_honesty_is_deterministic_and_seeded() -> None:
    a = _run(_request())[2]
    b = _run(_request())[2]
    assert a == b
    c = _run(_request().model_copy(update={"seed": 8}))[2]
    assert c["permutation"]["histogram"] != a["permutation"]["histogram"] or (
        c["bootstrap"]["roi_ci90"] != a["bootstrap"]["roi_ci90"]
    )


def test_bootstrap_interval_brackets_the_mean_and_handles_no_trades() -> None:
    rng = np.random.default_rng(0)
    trades = [
        {"fill_status": "FILLED", "net_pnl": str(v)} for v in rng.normal(1.0, 5.0, size=200)
    ] + [{"fill_status": "UNEXECUTABLE", "net_pnl": "0"}]
    boot = H.bootstrap(trades, Decimal("1000"), 10_000, np.random.default_rng(1))
    lo, hi = boot["roi_ci90"]
    assert lo < boot["roi"] < hi
    assert boot["n_trades"] == 200 and not boot["low_sample"]
    elo, ehi = boot["expectancy_ci95"]
    assert elo < boot["expectancy"] < ehi
    # sanity against the normal approximation: 90% half-width ≈ 1.645 σ√n / capital
    assert hi - lo == pytest.approx(2 * 1.645 * 5.0 * np.sqrt(200) / 1000, rel=0.15)

    empty = H.bootstrap([], Decimal("1000"), 10_000, np.random.default_rng(1))
    assert empty["roi_ci90"] is None and empty["note"] == "no executed trades to resample"


def test_no_signals_means_no_null_distribution() -> None:
    request = _request()
    request = request.model_copy(
        update={
            "strategy": request.strategy.model_copy(
                update={
                    "entry": request.strategy.entry.model_copy(
                        update={
                            "all": [
                                request.strategy.entry.all[0].model_copy(update={"value": 5.0})  # type: ignore[index]
                            ]
                        }
                    )
                }
            )
        }
    )
    _, result, h = _run(request)
    assert result.metrics["n_trades"] == 0
    assert h["permutation"]["percentile"] is None
    assert h["permutation"]["note"] == "the strategy produced no signals to permute"
    assert h["summary"]["headline"].startswith("No permutation test")
    assert h["buy_and_hold"]["n_trades"] == 2  # one position per market


def test_share_beaten_counts_ties_half() -> None:
    assert H.share_beaten(1.0, np.array([0.0, 1.0, 2.0, 1.0])) == 50.0
    assert H.share_beaten(1.0, np.array([])) is None


def test_lean_replay_matches_when_holding_to_resolution() -> None:
    """Without exit rules a lean run skips bars while holding; it must still land on every
    settlement and reproduce the real run exactly."""
    ds, st = _planted()
    st = SettlementTable(
        {
            "a": Settlement("YES", T0 + 90 * 60, T0 + 88 * 60),
            "b": Settlement("NO", T0 + 95 * 60, None),
        }
    )
    request = _request(permutations=0, random_baselines=0)
    request = request.model_copy(
        update={
            "strategy": request.strategy.model_copy(
                update={"exit": request.strategy.exit.model_copy(update={"any": []})}
            )
        }
    )
    prep = prepare(request, ds, st)
    result = simulate(prep)
    assert isinstance(result, BacktestResult)
    assert result.metrics["exit_reasons"] == {"MARKET_RESOLVED": result.metrics["n_trades"]}
    replay = simulate(prep, forced=H._schedule(prep, H._real_episodes(prep, result), "REPLAY"))
    assert isinstance(replay, LeanOutcome)
    assert replay.roi == result.metrics["roi"] and replay.n_trades == result.metrics["n_trades"]
