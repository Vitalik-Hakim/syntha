"""Property tests (PRD §17.2) over randomly generated markets, strategies and execution settings.

Invariants are also asserted inside the engine at every bar (check_invariants=True): cash never
negative, exact closed-form cash reconciliation, position limits. A violation raises and fails
the example.
"""

from decimal import Decimal

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.data import Dataset, MarketTerms, Settlement, SettlementTable, TokenData
from syntha_engine.runner import run
from syntha_engine.serialize import canonical_json

T0 = 1_767_225_600
N_MIN = 90


@st.composite
def scenario(draw: st.DrawFn) -> tuple[BacktestRequest, Dataset, SettlementTable]:
    n_markets = draw(st.integers(1, 3))
    markets, tokens, settlements = {}, {}, {}
    for m in range(n_markets):
        cid = f"m{m}"
        min_size = draw(st.sampled_from([None, "1", "5", "50"]))
        fees = draw(st.booleans())
        markets[cid] = MarketTerms(
            cid,
            draw(st.sampled_from(["e0", "e1", f"e-{cid}"])),
            "test",
            Decimal("0.01"),
            None if min_size is None else Decimal(min_size),
            T0,
            T0,
            T0 + N_MIN * 60,
            fees,
            "t_fees" if fees else None,
            Decimal("0.05") if fees else None,
            1,
        )
        resolution = draw(st.sampled_from(["YES", "NO", "INVALID", "UNRESOLVED"]))
        resolved = (
            None if resolution == "UNRESOLVED" else T0 + draw(st.integers(10, N_MIN + 10)) * 60
        )
        settlements[cid] = Settlement(resolution, resolved, resolved)
        for idx in (0, 1):
            steps = draw(st.lists(st.integers(-3, 3), min_size=N_MIN, max_size=N_MIN))
            p = np.clip(0.5 + np.cumsum(steps) / 100, 0.02, 0.98)
            keep = np.array(draw(st.lists(st.booleans(), min_size=N_MIN, max_size=N_MIN)))
            minutes = np.flatnonzero(keep | (np.arange(N_MIN) == 0))
            k = draw(st.integers(0, 60))
            pr_ts = np.sort(
                T0
                + np.array(
                    draw(st.lists(st.integers(0, N_MIN * 60 - 1), min_size=k, max_size=k)),
                    dtype=np.int64,
                )
            )
            tid = f"{cid}-{idx}"
            tokens[tid] = TokenData(
                tid,
                cid,
                idx,
                ["Yes", "No"][idx],
                T0 + minutes.astype(np.int64) * 60,
                np.round(p[minutes], 2),
                pr_ts,
                np.array([draw(st.integers(2, 98)) / 100 for _ in range(k)]),
                np.array([float(draw(st.integers(1, 400))) for _ in range(k)]),
                np.ones(k, dtype=np.int8),
            )
    dsl = StrategyDSL.model_validate(
        {
            "name": "prop",
            "universe": {"side": draw(st.sampled_from(["BUY_YES", "BUY_NO"]))},
            "entry": {
                "all": [
                    {"feature": "price", "op": "lte", "value": draw(st.floats(0.1, 0.95))},
                    {
                        "feature": "price_change_pct",
                        "params": {"lookback_minutes": 2},
                        "op": "gte",
                        "value": draw(st.floats(-0.2, 0.05)),
                    },
                ]
            },
            "sizing": draw(
                st.sampled_from(
                    [
                        {"mode": "FIXED_CASH", "value": 20},
                        {"mode": "PCT_OF_EQUITY", "value": 0.3},
                        {"mode": "PCT_OF_INITIAL", "value": 0.5},
                        {"mode": "FIXED_SHARES", "value": 60},
                        {"mode": "PCT_OF_EQUITY", "value": 0.9, "max_notional_per_trade": 45},
                    ]
                )
            ),
            "exit": {
                "any": draw(
                    st.sampled_from(
                        [
                            [],
                            [
                                {"type": "TAKE_PROFIT_PCT", "value": 0.05},
                                {"type": "STOP_LOSS_PCT", "value": 0.05},
                            ],
                            [{"type": "MAX_HOLD_MINUTES", "value": 3}],
                            [{"type": "PRICE_ABOVE", "value": 0.6}],
                        ]
                    )
                )
            },
            "constraints": {
                "max_open_positions": draw(st.integers(1, 4)),
                "max_total_exposure_pct": draw(st.floats(0.2, 1.0)),
                "max_positions_per_event": draw(st.integers(1, 2)),
                "cooldown_minutes_per_market": draw(st.integers(0, 5)),
                "allow_reentry_same_market": draw(st.booleans()),
            },
        }
    )
    execution = draw(
        st.sampled_from(
            [
                {"model": "LAST_PRICE", "slippage_ticks": 0, "liquidity_participation": None},
                {
                    "model": "TRADE_PRINT",
                    "slippage_ticks": 1,
                    "liquidity_participation": 0.5,
                    "partial_fill_policy": "ALLOW",
                },
                {
                    "model": "TRADE_PRINT",
                    "slippage_ticks": 2,
                    "liquidity_participation": 0.2,
                    "partial_fill_policy": "REJECT",
                    "adverse_selection": True,
                },
                {
                    "model": "TRADE_PRINT",
                    "slippage_bps": 50,
                    "slippage_ticks": None,
                    "liquidity_participation": 1.0,
                    "allow_fill_on_no_prints": True,
                },
            ]
        )
    )
    request = BacktestRequest.model_validate(
        {
            "strategy": dsl.model_dump(mode="json"),
            "starting_capital": draw(st.sampled_from(["100", "37.5", "1000"])),
            "decision_lag_bars": draw(st.integers(0, 2)),
            "execution": execution,
            "fees": draw(
                st.sampled_from(
                    [
                        {"mode": "CURRENT"},
                        {"mode": "FIXED", "fixed_rate": "0.07", "fixed_exponent": 2},
                    ]
                )
            ),
            "max_stale_minutes": draw(st.sampled_from([1, 5, 60])),
        }
    )
    return request, Dataset(markets, tokens), SettlementTable(settlements)


PROPS = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@PROPS
@given(scenario())
def test_cash_never_negative_and_equity_identity_holds(
    s: tuple[BacktestRequest, Dataset, SettlementTable],
) -> None:
    request, dataset, settlement = s
    result = run(request, dataset, settlement)  # engine asserts invariants at every bar
    for row in result.equity:
        assert Decimal(row["cash"]) >= 0
        assert Decimal(row["equity"]) == Decimal(row["cash"]) + Decimal(row["positions_value"])
    for entry in result.ledger:
        assert Decimal(entry["running_cash"]) >= 0


@PROPS
@given(scenario())
def test_executed_trades_match_buy_ledger_entries(
    s: tuple[BacktestRequest, Dataset, SettlementTable],
) -> None:
    request, dataset, settlement = s
    result = run(request, dataset, settlement)
    executed = [t for t in result.trades if t["fill_status"] in ("FILLED", "PARTIAL")]
    buys = [e for e in result.ledger if e["type"] == "BUY"]
    assert len(executed) == len(buys)
    for t in result.trades:
        if t["fill_status"] in ("UNEXECUTABLE", "REJECTED_MIN_SIZE"):
            assert Decimal(t["net_pnl"]) == 0 and Decimal(t["shares"]) == 0


@PROPS
@given(scenario())
def test_market_order_in_the_request_does_not_change_results(
    s: tuple[BacktestRequest, Dataset, SettlementTable],
) -> None:
    request, dataset, settlement = s
    forward = run(request, dataset, settlement)
    reversed_ds = Dataset(
        dict(reversed(list(dataset.markets.items()))), dict(reversed(list(dataset.tokens.items())))
    )
    backward = run(
        request, reversed_ds, SettlementTable(dict(reversed(list(settlement.by_condition.items()))))
    )
    assert canonical_json(forward.metrics) == canonical_json(backward.metrics)
    assert canonical_json(forward.trades) == canonical_json(backward.trades)
