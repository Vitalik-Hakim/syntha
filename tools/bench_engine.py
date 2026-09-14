#!/usr/bin/env python3
"""Engine benchmark against the PRD §9.2 target: 1,000 markets × 90 days of 1-minute bars in
under 60 seconds on a laptop.

Synthetic data with the target's shape: every token has a price sample every minute and
realistic sparse prints; the strategy is the built-in Underdog (rolling features plus exits).
Measures the engine only (no database load).

Usage: python tools/bench_engine.py [n_markets] [days]
"""

from __future__ import annotations

import resource
import sys
import time
from decimal import Decimal

import numpy as np

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.builtins import builtin_strategy
from syntha_engine.data import Dataset, MarketTerms, Settlement, SettlementTable, TokenData
from syntha_engine.runner import run

T0 = 1_767_225_600


def build(n_markets: int, days: int, seed: int = 0) -> tuple[Dataset, SettlementTable]:
    rng = np.random.default_rng(seed)
    n = days * 1440
    ts = T0 + np.arange(n, dtype=np.int64) * 60
    markets, tokens, settle = {}, {}, {}
    for m in range(n_markets):
        cid = f"m{m:04d}"
        markets[cid] = MarketTerms(
            cid,
            f"e{m}",
            "sports",
            Decimal("0.01"),
            Decimal("5"),
            T0,
            T0,
            T0 + (days + 1) * 86_400,
            True,
            "sports_fees_v2",
            Decimal("0.03"),
            1,
        )
        steps = rng.normal(0, 0.004, n)
        price = np.clip(0.15 + np.cumsum(steps), 0.01, 0.99).round(3)
        k = n // 50  # prints in ~2% of minutes, busier than the real sample (0.45%)
        pts = np.sort(T0 + rng.integers(0, n * 60, k))
        tokens[f"{cid}-yes"] = TokenData(
            f"{cid}-yes",
            cid,
            0,
            "Yes",
            ts,
            price,
            pts,
            np.clip(price[(pts - T0) // 60] + rng.normal(0, 0.005, k), 0.01, 0.99),
            rng.integers(5, 500, k).astype(np.float64),
            np.ones(k, dtype=np.int8),
            prior_notional=20_000.0,
        )
        settle[cid] = Settlement(
            "YES" if rng.random() < 0.2 else "NO", T0 + days * 86_400, T0 + days * 86_400
        )
    return Dataset(markets, tokens), SettlementTable(settle)


def main() -> None:
    n_markets = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    t0 = time.perf_counter()
    dataset, settlement = build(n_markets, days)
    t1 = time.perf_counter()
    request = BacktestRequest.model_validate(
        {
            "strategy": builtin_strategy("underdog_probability_reversion").model_dump(mode="json"),
            "starting_capital": "1000",
            "execution": {
                "model": "TRADE_PRINT",
                "slippage_ticks": 1,
                "liquidity_participation": 0.1,
            },
        }
    )
    result = run(request, dataset, settlement)
    t2 = time.perf_counter()
    bars = n_markets * days * 1440
    rss_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
    dq = result.data_quality
    print(
        f"markets={n_markets} days={days} bars={bars:,} build={t1 - t0:.1f}s engine={t2 - t1:.1f}s "
        f"({bars / (t2 - t1) / 1e6:.2f}M bars/s) peak_rss={rss_gb:.2f}GB "
        f"signals={dq['signals_generated']} filled={dq['signals_filled']} trades={result.metrics['n_trades']}"
    )


if __name__ == "__main__":
    main()
