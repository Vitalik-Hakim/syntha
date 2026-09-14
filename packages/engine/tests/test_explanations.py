"""Explanations (PRD §13): rule traces that carry every check with its actual value, and near
misses that say why the strategy did not trade at a bar."""

import dataclasses
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from syntha_contracts.backtest import BacktestRequest
from syntha_engine import nearmiss as NM
from syntha_engine.data import Dataset, MarketTerms, Settlement, SettlementTable, TokenData
from syntha_engine.runner import run

T0 = 1_767_225_600


def _market(cid: str, event: str) -> MarketTerms:
    return MarketTerms(
        cid, event, "test", Decimal("0.01"), Decimal("1"), T0, T0, None, False, None, None, None
    )


def _token(cid: str, prices: list[float], prints: list[tuple[int, float, float]] = ()) -> TokenData:  # type: ignore[assignment]
    n = len(prices)
    return TokenData(
        f"{cid}-yes",
        cid,
        0,
        "Yes",
        T0 + np.arange(n, dtype=np.int64) * 60,
        np.array(prices),
        np.array([T0 + m * 60 for m, _, _ in prints], dtype=np.int64),
        np.array([p for _, p, _ in prints]),
        np.array([s for _, _, s in prints]),
        np.ones(len(prints), dtype=np.int8),
    )


def _dataset(markets: dict[str, tuple[str, TokenData]]) -> tuple[Dataset, SettlementTable]:
    return (
        Dataset(
            {cid: _market(cid, event) for cid, (event, _) in markets.items()},
            {tok.clob_token_id: tok for _, tok in markets.values()},
        ),
        SettlementTable({cid: Settlement("UNRESOLVED", None, None) for cid in markets}),
    )


def _request(strategy: dict[str, Any], **extra: Any) -> BacktestRequest:
    base: dict[str, Any] = {
        "name": "t",
        "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.30}]},
        "sizing": {"mode": "FIXED_CASH", "value": 5},
    }
    return BacktestRequest.model_validate(
        {"strategy": {**base, **strategy}, "execution": {"model": "LAST_PRICE"}, **extra}
    )


# ------------------------------------------------------------------ selection primitives


def test_nearest_spreads_picks_and_breaks_ties_to_the_earlier_bar() -> None:
    score = np.array([0.5, 0.5, 0.1, 0.5, 0.5, 0.2, 0.5, 0.5])
    picks = NM.nearest(score, np.arange(8), 3, separation=2)
    # 2 (0.1) first; 5 (0.2) is 3 bars away; then the earliest 0.5 at least 2 bars from both: 0.
    assert picks == [0, 2, 5]
    assert NM.nearest(score, np.arange(8), 3, separation=8) == [2]


def test_nearest_is_exact_when_the_pool_is_partial() -> None:
    rng = np.random.default_rng(1)
    score = rng.random(100_000)
    picks = NM.nearest(score, np.arange(score.size), 2, separation=1)
    assert picks == sorted(np.argsort(score, kind="stable")[:2].tolist())


def test_stretch_starts_are_evenly_spaced() -> None:
    mask = np.zeros(100, dtype=bool)
    for start in (3, 20, 40, 60, 80):
        mask[start : start + 5] = True
    assert NM.stretch_starts(mask, 2) == [3, 80]
    assert NM.stretch_starts(mask, 9) == [3, 20, 40, 60, 80]
    assert NM.stretch_starts(np.zeros(5, dtype=bool), 2) == []


def test_run_cap_keeps_the_nearest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(NM.PER_RUN, NM.RULES_FAILED, 2)
    rows = [
        {"kind": NM.RULES_FAILED, "score": s, "ts": f"t{i}", "clob_token_id": "x"}
        for i, s in enumerate([1.5, 1.1, 1.3])
    ]
    kept, capped = NM.cap_run(rows)
    assert capped and [r["score"] for r in kept] == [1.1, 1.3]


# ------------------------------------------------------------------ near misses in a run


def test_rules_failed_near_misses_are_the_closest_bars() -> None:
    prices = [0.5] * 200
    prices[100], prices[130] = 0.31, 0.305  # never ≤ 0.30: no signal at all
    ds, st = _dataset({"m": ("e", _token("m", prices))})
    r = run(_request({}), ds, st)
    assert r.trades == []
    rows = [x for x in r.near_misses if x["kind"] == NM.RULES_FAILED]
    # Bar 130 is nearest; 100 is within the 60-bar separation of it; then the earliest ties.
    assert [x["ts"] for x in rows] == [
        "2026-01-01T00:00:00Z",
        "2026-01-01T01:00:00Z",
        "2026-01-01T02:10:00Z",
    ]
    near = rows[-1]
    assert near["failed_clauses"] == 1 and near["blocked_by"] is None
    assert near["score"] == pytest.approx(1 + (0.005 / 0.30) / 2)
    clause = near["trace"]["clauses"][0]
    assert (clause["actual"], clause["threshold"], clause["passed"]) == (0.305, 0.30, False)
    assert near["trace"]["decision"] == "REJECT"
    assert all(c["passed"] for c in near["trace"]["universe"])
    assert [x["seq"] for x in r.near_misses] == list(range(1, len(r.near_misses) + 1))


def test_universe_blocked_near_miss_names_the_filter() -> None:
    ds, st = _dataset({"m": ("e", _token("m", [0.2] * 30))})
    r = run(_request({"universe": {"min_volume_asof": 1000}}), ds, st)
    assert r.trades == []
    [row] = r.near_misses
    assert (row["kind"], row["blocked_by"], row["ts"]) == (
        NM.BLOCKED_BY_UNIVERSE,
        "below_min_volume_asof",
        "2026-01-01T00:00:00Z",
    )
    volume = next(c for c in row["trace"]["universe"] if c["filter"] == "min_volume_asof")
    assert (volume["actual"], volume["passed"]) == (0.0, False)
    assert all(c["passed"] for c in row["trace"]["clauses"])


def test_constraint_blocked_near_miss_names_the_constraint() -> None:
    ds, st = _dataset({"a": ("e1", _token("a", [0.2] * 10)), "b": ("e2", _token("b", [0.2] * 10))})
    r = run(_request({"constraints": {"max_open_positions": 1}}), ds, st)
    blocked = [x for x in r.near_misses if x["kind"] == NM.BLOCKED_BY_CONSTRAINT]
    # b is blocked from bar 0 for the whole run: one stretch, one near miss.
    assert [(x["clob_token_id"], x["ts"], x["blocked_by"]) for x in blocked] == [
        ("b-yes", "2026-01-01T00:00:00Z", "max_open_positions")
    ]


def test_random_entries_have_no_near_misses() -> None:
    ds, st = _dataset({"m": ("e", _token("m", [0.5] * 30))})
    r = run(_request({"entry": {"random": {"probability_per_bar": 0.1}}}), ds, st)
    assert r.near_misses == []
    assert r.data_quality["near_misses"]["available"] is False


# ------------------------------------------------------------------ trade traces


def test_trade_trace_carries_universe_windows_exit_plan_and_exit_checklist() -> None:
    prices = [0.5] * 20 + [0.20, 0.20, 0.20] + [0.28] * 10  # fill 0.21 → take profit at 0.273
    prints = [(m, 0.5, 10.0) for m in range(15)]
    ds, st = _dataset({"m": ("e", _token("m", prices, prints))})
    request = _request(
        {
            "universe": {"min_volume_asof": 10},
            "entry": {
                "all": [
                    {"feature": "price", "op": "lte", "value": 0.30},
                    {
                        "feature": "print_count",
                        "params": {"lookback_minutes": 30},
                        "op": "gte",
                        "value": 1,
                    },
                ]
            },
            "exit": {
                "any": [
                    {"type": "STOP_LOSS_PCT", "value": 0.5},
                    {"type": "TAKE_PROFIT_PCT", "value": 0.3},
                    {"type": "MAX_HOLD_MINUTES", "value": 600},
                ]
            },
        }
    )
    r = run(request, ds, st)
    trade = next(t for t in r.trades if t["fill_status"] == "FILLED")
    entry = trade["entry_rule_trace"]
    assert entry["ts"] == "2026-01-01T00:20:00Z"
    assert [c["filter"] for c in entry["universe"]] == [
        "market_open",
        "min_volume_asof",
        "max_stale_minutes",
    ]
    assert entry["universe"][1]["actual"] == pytest.approx(75.0)  # 15 prints × 0.5 × 10
    window = entry["clauses"][1]["window"]
    # print_count at bar 00:20 reads the 30 minutes up to that bar's close.
    assert window == {"from": "2025-12-31T23:51:00Z", "to": "2026-01-01T00:21:00Z"}
    fill = float(trade["entry_price"])
    plan = {p["type"]: p for p in entry["exit_plan"]}
    assert plan["STOP_LOSS_PCT"]["level"] == pytest.approx(fill * 0.5)
    assert plan["TAKE_PROFIT_PCT"]["level"] == pytest.approx(fill * 1.3)
    assert plan["MAX_HOLD_MINUTES"]["deadline"] == "2026-01-01T10:21:00Z"
    exit_trace = trade["exit_rule_trace"]
    assert exit_trace["reason"] == "TAKE_PROFIT"
    assert [c["type"] for c in exit_trace["checks"]] == [
        "STOP_LOSS_PCT",
        "TAKE_PROFIT_PCT",
        "MAX_HOLD_MINUTES",
    ]
    assert [c["passed"] for c in exit_trace["checks"]] == [False, True, False]
    assert exit_trace["checks"][1].get("fired") is True and exit_trace["rule"]["fired"] is True
    [fill_record] = exit_trace["fills"]
    assert (
        fill_record["signal_ts"] == exit_trace["ts"]
        and fill_record["signal_reason"] == "TAKE_PROFIT"
    )


def test_exit_reason_breakdown_sums_to_the_totals() -> None:
    from syntha_engine.metrics.core import exit_reason_breakdown

    def trade(reason: str | None, net: str, hold: int | None = 60) -> dict[str, Any]:
        return {
            "exit_reason": reason,
            "net_pnl": net,
            "gross_pnl": net,
            "fees_total": "0.1",
            "slippage_cost": "0.2",
            "return_pct": "0.01",
            "holding_seconds": hold,
        }

    rows = exit_reason_breakdown(
        [
            trade("STOP_LOSS", "-1"),
            trade("STOP_LOSS", "-2"),
            trade("TAKE_PROFIT", "3"),
            trade(None, "0.5", None),
        ]
    )
    assert [(r["reason"], r["trades"], r["wins"]) for r in rows] == [
        ("STOP_LOSS", 2, 0),
        ("OPEN", 1, 1),
        ("TAKE_PROFIT", 1, 1),
    ]
    assert sum(r["net_pnl"] for r in rows) == pytest.approx(0.5)
    assert rows[1]["median_holding_seconds"] is None


# ------------------------------------------------------------------ minimum order size


def _min_size_request(policy: str, **sizing: Any) -> BacktestRequest:
    return _request(
        {
            "sizing": {"mode": "FIXED_CASH", "value": 4.8, "min_order_policy": policy, **sizing},
            "entry": {"all": [{"feature": "price", "op": "gte", "value": 0.95}]},
        }
    )


def _min_size_dataset(min_size: str = "5") -> tuple[Dataset, SettlementTable]:
    ds, st = _dataset({"m": ("e", _token("m", [0.98] * 5))})
    terms = ds.markets["m"]
    market = dataclasses.replace(terms, min_order_size=Decimal(min_size))
    return Dataset({"m": market}, ds.tokens), st


def test_skip_rejects_below_minimum_and_says_rounding_up_would_fill() -> None:
    ds, st = _min_size_dataset()
    r = run(_min_size_request("SKIP"), ds, st)  # $4.80 at a 0.99 fill = 4.84 shares < 5
    rejected = [t for t in r.trades if t["fill_status"] == "REJECTED_MIN_SIZE"]
    assert rejected, r.trades
    sizing = rejected[0]["entry_rule_trace"]["sizing"]
    assert sizing["below_min_cause"] == "sizing" and sizing["round_up_blocked_by"] is None
    assert r.data_quality["signals_rejected_min_size_by_cause"] == {"sizing": 1}
    assert r.data_quality["min_size_rejections_round_up_would_fill"] == 1


def test_round_up_places_the_minimum_when_limits_allow() -> None:
    ds, st = _min_size_dataset()
    r = run(_min_size_request("ROUND_UP"), ds, st)
    [trade] = [t for t in r.trades if t["fill_status"] == "FILLED"]
    assert trade["shares"] == "5.000000"
    sizing = trade["entry_rule_trace"]["sizing"]
    assert (
        sizing["rounded_up_to_min_size"] is True
        and sizing["size_binding_constraint"] == "min_order_size"
    )
    assert r.data_quality["entries_rounded_up_to_min_size"] == 1


def test_round_up_respects_the_overshoot_limit_and_hard_caps() -> None:
    ds, st = _min_size_dataset(min_size="20")  # 20 shares ≈ $19.80, over 2× the $4.80 requested
    r = run(_min_size_request("ROUND_UP"), ds, st)
    [row] = r.trades
    assert row["fill_status"] == "REJECTED_MIN_SIZE"
    assert row["entry_rule_trace"]["sizing"]["round_up_blocked_by"] == "sizing"
    r = run(
        _min_size_request("ROUND_UP", round_up_max_multiple=5, max_notional_per_trade=10), ds, st
    )
    [row] = r.trades
    assert row["entry_rule_trace"]["sizing"]["round_up_blocked_by"] == "max_notional_per_trade"


# ------------------------------------------------------------------ EITHER side


def test_either_side_buys_the_qualifying_token_and_never_both() -> None:
    """One market, YES at 0.20 and NO at 0.80: a 'price >= 0.75' rule on EITHER buys NO; a
    'price <= 0.25' rule buys YES; neither ever holds both sides."""
    yes = _token("m", [0.20] * 30)
    no = TokenData(
        "m-no",
        "m",
        1,
        "No",
        yes.bar_ts,
        np.array([0.80] * 30),
        yes.print_ts,
        yes.print_price,
        yes.print_size,
        yes.print_side,
    )
    ds = Dataset({"m": _market("m", "e")}, {"m-yes": yes, "m-no": no})
    st = SettlementTable({"m": Settlement("UNRESOLVED", None, None)})
    high = _request(
        {
            "universe": {"side": "EITHER"},
            "entry": {"all": [{"feature": "price", "op": "gte", "value": 0.75}]},
        }
    )
    low = _request(
        {
            "universe": {"side": "EITHER"},
            "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.25}]},
        }
    )
    any_price = _request(
        {
            "universe": {"side": "EITHER"},
            "entry": {"all": [{"feature": "price", "op": "gt", "value": 0.0}]},
        }
    )
    assert [(t["clob_token_id"], t["side"]) for t in run(high, ds, st).trades] == [
        ("m-no", "BUY_NO")
    ]
    assert [(t["clob_token_id"], t["side"]) for t in run(low, ds, st).trades] == [
        ("m-yes", "BUY_YES")
    ]
    both = run(any_price, ds, st)
    assert len(both.trades) == 1  # the second token is 'already_holding': one position per market
    assert both.data_quality["signals_blocked_by_constraint"]["already_holding"] >= 1
