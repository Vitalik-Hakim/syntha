"""Signal episodes: a continuously true entry condition is one signal, however many bars it takes
to fill; each episode that never fills is one trade row carrying its attempt count."""

from decimal import Decimal

import numpy as np

from syntha_contracts.backtest import BacktestRequest
from syntha_engine.data import Dataset, MarketTerms, Settlement, SettlementTable, TokenData
from syntha_engine.runner import run

T0 = 1_767_225_600


def _dataset(prices: list[float], min_size: str | None = "5") -> tuple[Dataset, SettlementTable]:
    terms = MarketTerms(
        "m",
        "e",
        "test",
        Decimal("0.01"),
        None if min_size is None else Decimal(min_size),
        T0,
        T0,
        None,
        False,
        None,
        None,
        None,
    )
    n = len(prices)
    tok = TokenData(
        "yes",
        "m",
        0,
        "Yes",
        T0 + np.arange(n, dtype=np.int64) * 60,
        np.array(prices),
        np.empty(0, np.int64),
        np.empty(0),
        np.empty(0),
        np.empty(0, np.int8),
    )
    return Dataset({"m": terms}, {"yes": tok}), SettlementTable(
        {"m": Settlement("UNRESOLVED", None, None)}
    )


def _request() -> BacktestRequest:
    return BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "ep",
                "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.30}]},
                "sizing": {"mode": "FIXED_CASH", "value": 5},
                # episode mechanics are defined on single-bar attempts; resting limit orders
                # (v3 E1) are covered by their own tests
                "order": {"type": "MARKET"},
            },
            "execution": {
                "model": "TRADE_PRINT",
                "slippage_ticks": 1,
                "liquidity_participation": 0.1,
            },
        }
    )


def test_lapsed_condition_starts_a_new_episode() -> None:
    # Condition true m0-m3, false m4, true m5-m7. No prints anywhere: every attempt is refused.
    ds, st = _dataset([0.3, 0.3, 0.3, 0.3, 0.5, 0.3, 0.3, 0.3])
    r = run(_request(), ds, st)
    rows = r.trades
    assert [t["fill_status"] for t in rows] == ["UNEXECUTABLE", "UNEXECUTABLE"]
    assert [t["signal_ts"] for t in rows] == ["2026-01-01T00:00:00Z", "2026-01-01T00:05:00Z"]
    # With a 1-bar decision lag an attempt signalled at bar k is still pending during bar k+1
    # (entries are evaluated before execution), and its refusal is only known after it runs. So
    # retries land every other bar: episode 1 attempts at m0 and m2; episode 2 at m5 and m7 (the
    # last still pending when the data ends).
    assert [t["entry_rule_trace"]["attempts"] for t in rows] == [2, 2]
    assert r.data_quality["signals_generated"] == 2
    assert r.data_quality["signals_unexecutable"] == 2
    assert r.data_quality["signals_pending_at_end"] == 1


def test_permanent_refusal_is_recorded_once_and_never_retried() -> None:
    ds, st = _dataset([0.3] * 10, min_size=None)
    r = run(_request(), ds, st)
    assert len(r.trades) == 1
    assert r.trades[0]["entry_rule_trace"]["execution"]["reason"] == "MIN_SIZE_UNKNOWN"
    assert r.trades[0]["entry_rule_trace"]["attempts"] == 1
    assert r.data_quality["signals_generated"] == 1


def test_default_horizon_runs_until_resolution_so_positions_settle() -> None:
    """Prices end shortly after trading closes; resolution can come much later. Without an
    explicit end, positions must settle rather than be valued at their last mark."""
    ds, _ = _dataset([0.3, 0.3, 0.3])
    settle = SettlementTable({"m": Settlement("YES", T0 + 45 * 60, T0 + 3 * 60)})
    request = BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "h",
                "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.30}]},
                "sizing": {"mode": "FIXED_CASH", "value": 3},
            },
            "execution": {
                "model": "LAST_PRICE",
                "slippage_ticks": 0,
                "liquidity_participation": None,
            },
        }
    )
    r = run(request, ds, settle)
    (trade,) = [t for t in r.trades if t["fill_status"] == "FILLED"]
    assert trade["exit_reason"] == "MARKET_RESOLVED"
    assert trade["exit_ts"] == "2026-01-01T00:45:00Z"
    assert r.data_quality["coverage"]["end"] == "2026-01-01T00:45:00Z"
