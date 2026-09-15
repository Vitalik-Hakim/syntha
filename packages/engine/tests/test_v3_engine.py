"""PRD v3 §3 (M8): cluster bootstrap, MDE and capacity fields, the fee counterfactual, and the
point-in-time EventView behind basket trades."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.builtins import builtin_strategy
from syntha_engine import honesty as H
from syntha_engine.data import Dataset, MarketTerms, Settlement, SettlementTable, TokenData
from syntha_engine.runner import BacktestResult, _iso, prepare, simulate

T0 = 1_767_225_600


def _trade(i: int, net: float, event: str, day: int) -> dict[str, Any]:
    return {
        "seq": i,
        "fill_status": "FILLED",
        "net_pnl": f"{net:.6f}",
        "notional": "10.000000",
        "condition_id": f"m{i}",
        "entry_ts": f"2026-01-{day:02d}T00:00:00+00:00",
        "_event": event,
    }


def _width(ci: list[float]) -> float:
    return ci[1] - ci[0]


def test_one_trade_per_cluster_reproduces_the_trade_level_interval() -> None:
    rng = np.random.default_rng(0)
    trades = [_trade(i, v, f"e{i}", 1 + i % 28) for i, v in enumerate(rng.normal(1.0, 5.0, 200))]
    labels = [t["_event"] for t in trades]
    flat = H.bootstrap(trades, Decimal("1000"), 5_000, np.random.default_rng(1))
    clustered = H.cluster_bootstrap(
        trades, labels, Decimal("1000"), 5_000, np.random.default_rng(1), "event"
    )
    assert clustered["n_clusters"] == 200
    assert clustered["roi_ci90"] == pytest.approx(flat["roi_ci90"], rel=1e-12)  # draw for draw
    assert clustered["mde_80_return_per_dollar"] == pytest.approx(
        H.MDE_MULTIPLIER * clustered["se_return_per_dollar"]
    )
    assert clustered["return_per_dollar"] == pytest.approx(
        sum(float(t["net_pnl"]) for t in trades) / 2000
    )


def test_correlated_clusters_widen_the_interval() -> None:
    # 10 clusters of 20 trades that share one draw: 200 rows, 10 independent observations
    rng = np.random.default_rng(2)
    draws = rng.normal(1.0, 5.0, 10)
    trades = [_trade(c * 20 + j, draws[c], f"e{c}", 1 + c) for c in range(10) for j in range(20)]
    labels = [t["_event"] for t in trades]
    flat = H.bootstrap(trades, Decimal("1000"), 5_000, np.random.default_rng(1))
    clustered = H.cluster_bootstrap(
        trades, labels, Decimal("1000"), 5_000, np.random.default_rng(1), "event"
    )
    assert clustered["n_clusters"] == 10
    # variance of a mean of 10 independent draws vs 200 pseudo-independent ones: √20 ≈ 4.5× wider
    assert _width(clustered["roi_ci90"]) > 3 * _width(flat["roi_ci90"])
    assert clustered["mde_80_return_per_dollar"] > 3 * (
        H.MDE_MULTIPLIER * float(np.std([float(t["net_pnl"]) / 10 for t in trades]) / np.sqrt(200))
    )


def test_cluster_bootstrap_handles_no_trades() -> None:
    empty = H.cluster_bootstrap([], [], Decimal("1000"), 100, np.random.default_rng(1), "day")
    assert empty["roi_ci90"] is None and empty["mde_80_return_per_dollar"] is None


# ------------------------------------------------------------------ whole-run fields


def _market(
    cid: str, event: str, *, neg_risk: bool, catch_all: bool = False, open_min: int = 0
) -> MarketTerms:
    return MarketTerms(
        cid,
        event,
        "test",
        Decimal("0.01"),
        Decimal("1"),
        T0 + open_min * 60,
        T0 + open_min * 60,
        None,
        False,
        None,
        None,
        None,
        is_catch_all=catch_all,
        neg_risk=neg_risk,
    )


def _token(cid: str, prices: list[float], start_min: int = 0) -> TokenData:
    n = len(prices)
    empty_i, empty_f = np.empty(0, np.int64), np.empty(0)
    return TokenData(
        f"{cid}-yes",
        cid,
        0,
        "Yes",
        T0 + (start_min + np.arange(n, dtype=np.int64)) * 60,
        np.array(prices),
        empty_i,
        empty_f,
        empty_f,
        np.empty(0, np.int8),
    )


def _basket_request(min_markets: int = 2, **overrides: Any) -> BacktestRequest:
    return BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "yes-set",
                "universe": {"side": "BUY_YES"},
                "entry": {
                    "basket": {
                        "kind": "NEGRISK_YES_SET",
                        "min_edge": 0.0,
                        "require_catch_all": True,
                        "min_markets": min_markets,
                    }
                },
                "sizing": {"mode": "FIXED_CASH", "value": 10},
                "exit": {"any": []},
                "constraints": {"max_open_positions": 2, "max_positions_per_event": 1},
                "order": {"type": "MARKET"},
            },
            "starting_capital": "100",
            "seed": 1,
            "execution": {
                "model": "LAST_PRICE",
                "slippage_ticks": 0,
                "liquidity_participation": None,
            },
            "honesty": {"permutations": 0, "random_baselines": 0, "bootstrap_iterations": 200},
            **overrides,
        }
    )


def _event_dataset(*, neg_risk: bool, catch_all_listed_at: int) -> tuple[Dataset, SettlementTable]:
    """A and B priced 0.30 from m0; the catch-all C (also 0.30) is listed at `catch_all_listed_at`."""
    n = 20
    markets = {
        "a": _market("a", "ev", neg_risk=neg_risk),
        "b": _market("b", "ev", neg_risk=neg_risk),
        "c": _market("c", "ev", neg_risk=neg_risk, catch_all=True, open_min=catch_all_listed_at),
    }
    tokens = {
        "a-yes": _token("a", [0.30] * n),
        "b-yes": _token("b", [0.30] * n),
        "c-yes": _token("c", [0.30] * (n - catch_all_listed_at), catch_all_listed_at),
    }
    settlement = SettlementTable({cid: Settlement("UNRESOLVED", None, None) for cid in markets})
    return Dataset(markets, tokens), settlement


def _run(request: BacktestRequest, ds: Dataset, st: SettlementTable) -> BacktestResult:
    result = simulate(prepare(request, ds, st))
    assert isinstance(result, BacktestResult)
    return result


def test_event_view_does_not_see_a_catch_all_listed_in_the_future() -> None:
    """A + B = 0.60 < 1 from m0, but the set is not exhaustive until C is listed at m5: the first
    basket signal is m5 (executed m6). Reading catch-all status from the future would buy at m1."""
    ds, st = _event_dataset(neg_risk=True, catch_all_listed_at=5)
    result = _run(_basket_request(), ds, st)
    legs = [t for t in result.trades if t["basket_role"] == "LEG"]
    assert {t["clob_token_id"] for t in legs} == {"a-yes", "b-yes", "c-yes"}
    assert {t["entry_ts"] for t in legs} == {_iso(T0 + 6 * 60)}
    summary = [t for t in result.trades if t["basket_role"] == "SUMMARY"]
    assert len(summary) == 1 and summary[0]["side"] == "BASKET"
    assert result.data_quality["baskets_filled"] == 1
    # the basket is one position for the constraints, and one trade for the statistics
    assert result.metrics["n_trades"] == 1


def test_a_catch_all_listed_from_the_start_is_bought_at_once() -> None:
    ds, st = _event_dataset(neg_risk=True, catch_all_listed_at=0)
    result = _run(_basket_request(), ds, st)
    legs = [t for t in result.trades if t["basket_role"] == "LEG"]
    assert len(legs) == 3 and {t["entry_ts"] for t in legs} == {_iso(T0 + 60)}


def test_only_neg_risk_events_are_sets() -> None:
    ds, st = _event_dataset(neg_risk=False, catch_all_listed_at=0)
    result = _run(_basket_request(), ds, st)
    assert result.trades == [] and result.data_quality["baskets_signalled"] == 0


def test_metrics_and_honesty_carry_the_e5_fields() -> None:
    ds, st = _event_dataset(neg_risk=True, catch_all_listed_at=0)
    request = _basket_request()
    prep = prepare(request, ds, st)
    result = simulate(prep)
    assert isinstance(result, BacktestResult)
    m = result.metrics
    assert set(m["capacity"]) >= {"1000", "10000", "signals", "months"}
    assert m["return_per_dollar"]["pooled"] is not None
    h = H.compute_honesty(
        prep, result, buy_and_hold_dsl=builtin_strategy("buy_and_hold_to_resolution")
    )
    s = h["summary"]
    assert s["cluster_key"] in H.CLUSTER_KEYS and s["deciding_fee_mode"] == "CURRENT"
    assert s["mde_80"] is not None and s["cluster_ci90"] is not None
    assert h["fee_counterfactual"]["HISTORICAL"]["roi"] == m["roi"]  # the run's own mode
    assert "CURRENT" in h["fee_counterfactual"]
    attached = H.honesty_metrics(h)
    assert {"mde_80", "cluster_ci90", "fee_counterfactual"} <= set(attached)


# ------------------------------------------------------------------ M9 decisions


def test_capacity_months_come_from_the_collection_coverage_window() -> None:
    ds, st = _event_dataset(neg_risk=True, catch_all_listed_at=0)
    windowed = Dataset(ds.markets, ds.tokens, coverage=(T0, T0 + 61 * 86_400))
    result = _run(_basket_request(), windowed, st)
    cap = result.metrics["capacity"]
    assert cap["months"] == pytest.approx(61 / 30.4375)
    assert cap["denominator"] == "collection coverage window"
    fallback = _run(_basket_request(), ds, st).metrics["capacity"]
    assert fallback["denominator"].startswith("signal span") and fallback["months"] == 1 / 30.4375


def test_baskets_are_refused_on_final_volume_filtered_collections() -> None:
    ds, st = _event_dataset(neg_risk=True, catch_all_listed_at=0)
    filtered = Dataset(
        ds.markets,
        ds.tokens,
        selection={"selection_uses_final_volume": {"min_lifetime_volume": 10000.0}},
    )
    with pytest.raises(ValueError, match="refused on a collection selected by final volume"):
        prepare(_basket_request(), filtered, st)
    # an ordinary strategy on the same collection is only warned, never refused
    plain = BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "plain",
                "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.5}]},
                "sizing": {"mode": "FIXED_CASH", "value": 5},
            },
            "execution": {
                "model": "LAST_PRICE",
                "slippage_ticks": 0,
                "liquidity_participation": None,
            },
        }
    )
    assert prepare(plain, filtered, st) is not None
