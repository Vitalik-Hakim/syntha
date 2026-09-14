"""Result metrics with the exact definitions of PRD §12.

Ambiguity is how backtesting tools mislead, so every metric here is defined in `DEFINITIONS`
(shown as tooltips later) and suppressed rather than faked when the data can't support it.
Bootstrap confidence intervals and null-hypothesis comparisons are M5 (PRD §15).
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

LOW_SAMPLE_TRADES = 30
MIN_DAYS_FOR_CAGR = 90
MIN_DAILY_OBS_FOR_SHARPE = 30
COST_RATIO_WARNING = 0.5

DEFINITIONS = {
    "net_profit": "ending equity − starting capital",
    "roi": "net_profit / starting capital",
    "cagr": "annualised ROI; only computed when the backtest spans ≥ 90 days",
    "max_drawdown": "largest peak-to-trough fall of the mark-to-market equity curve (incl. unrealized P&L), sampled every bar",
    "sharpe": "mean / stdev of daily equity returns × √252, risk-free 0; needs ≥ 30 daily observations. Prediction-market returns are lumpy and non-normal: indicative only",
    "sortino": "as sharpe, with downside deviation",
    "win_rate": "trades with net_pnl > 0 / executed trades (FILLED or PARTIAL); UNEXECUTABLE and REJECTED_MIN_SIZE excluded",
    "profit_factor": "Σ net P&L of winning trades / |Σ net P&L of losing trades|; 'inf' when there are no losers",
    "expectancy": "mean net P&L per executed trade",
    "cost_ratio": "(fees + slippage) / |gross P&L|",
    "gross_pnl": "shares × (exit reference price − entry reference price)",
    "slippage_cost": "shares × |fill price − reference price|, entry and exit",
}


@dataclass(frozen=True, slots=True)
class EquityPoint:
    ts: int
    equity: Decimal


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


def max_drawdown(points: Sequence[EquityPoint]) -> dict[str, Any]:
    if not points:
        return {"usd": 0.0, "pct": 0.0, "peak_ts": None, "trough_ts": None, "duration_seconds": 0}
    peak = points[0]
    worst = (Decimal(0), Decimal(0), points[0], points[0])
    for pt in points:
        if pt.equity > peak.equity:
            peak = pt
        dd = peak.equity - pt.equity
        if dd > worst[0]:
            pct = dd / peak.equity if peak.equity > 0 else Decimal(0)
            worst = (dd, pct, peak, pt)
    dd, pct, pk, tr = worst
    return {
        "usd": float(dd),
        "pct": float(pct),
        "peak_ts": pk.ts if dd > 0 else None,
        "trough_ts": tr.ts if dd > 0 else None,
        "duration_seconds": (tr.ts - pk.ts) if dd > 0 else 0,
    }


def daily_returns(points: Sequence[EquityPoint]) -> list[float]:
    by_day: dict[int, Decimal] = {}
    for pt in points:
        by_day[pt.ts // 86_400] = pt.equity  # last equity of each UTC day
    values = [by_day[d] for d in sorted(by_day)]
    return [float(b / a - 1) for a, b in itertools.pairwise(values) if a > 0]


def _profit_factor(wins: list[Decimal], losses: list[Decimal]) -> float | str | None:
    if not losses:
        return "inf" if wins else None
    won = sum(wins, Decimal(0))
    lost = sum(losses, Decimal(0))
    return float(won / abs(lost))


def _histogram(values: list[float], bins: int = 20) -> dict[str, Any]:
    if not values:
        return {"edges": [], "counts": []}
    lo, hi = min(values), max(values)
    if lo == hi:
        return {"edges": [lo, hi], "counts": [len(values)]}
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(int((v - lo) / width), bins - 1)] += 1
    return {"edges": [lo + i * width for i in range(bins + 1)], "counts": counts}


def compute_metrics(
    *,
    starting_capital: Decimal,
    equity_points: Sequence[EquityPoint],
    trades: Sequence[dict[str, Any]],
    decision_lag_bars: int,
    execution_model: str,
    fee_mode: str,
    extra_warnings: Sequence[str] = (),
) -> dict[str, Any]:
    ending = equity_points[-1].equity if equity_points else starting_capital
    net_profit = ending - starting_capital
    roi = net_profit / starting_capital
    span_s = equity_points[-1].ts - equity_points[0].ts if len(equity_points) > 1 else 0
    span_days = span_s / 86_400
    cagr: float | None = None
    if span_days >= MIN_DAYS_FOR_CAGR and roi > -1:
        cagr = (1 + float(roi)) ** (365 / span_days) - 1

    rets = daily_returns(equity_points)
    sharpe = sortino = None
    if len(rets) >= MIN_DAILY_OBS_FOR_SHARPE:
        sd = statistics.stdev(rets)
        mean = statistics.fmean(rets)
        sharpe = mean / sd * math.sqrt(252) if sd > 0 else None
        downside = math.sqrt(statistics.fmean([min(r, 0.0) ** 2 for r in rets]))
        sortino = mean / downside * math.sqrt(252) if downside > 0 else None

    executed = [t for t in trades if t["fill_status"] in ("FILLED", "PARTIAL")]
    excluded = Counter(
        t["fill_status"] for t in trades if t["fill_status"] not in ("FILLED", "PARTIAL")
    )
    nets = [Decimal(t["net_pnl"]) for t in executed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross = sum((Decimal(t["gross_pnl"]) for t in executed), Decimal(0))
    fees = sum((Decimal(t["fees_total"]) for t in executed), Decimal(0))
    slippage = sum((Decimal(t["slippage_cost"]) for t in executed), Decimal(0))
    cost_ratio = float((fees + slippage) / abs(gross)) if gross != 0 else None

    streak = longest = 0
    for n in nets:
        streak = streak + 1 if n < 0 else 0
        longest = max(longest, streak)
    holding = [t["holding_seconds"] for t in executed if t["holding_seconds"] is not None]
    worst = min(executed, key=lambda t: Decimal(t["net_pnl"]), default=None)

    n_trades = len(executed)
    warnings: list[str] = list(extra_warnings)
    if decision_lag_bars == 0:
        warnings.append("SAME-BAR FILL — OPTIMISTIC")
    if execution_model == "LAST_PRICE":
        warnings.append("OPTIMISTIC — assumes fill at mid, unlimited size")
    if n_trades < LOW_SAMPLE_TRADES:
        warnings.append("Too few trades to distinguish this from luck.")
    if cost_ratio is not None and cost_ratio > COST_RATIO_WARNING:
        warnings.append(
            "Costs consumed more than half the gross edge. This strategy is highly "
            "sensitive to execution assumptions."
        )
    if fee_mode == "FIXED":
        warnings.append("COUNTERFACTUAL FEES — a fixed fee rate was applied to every fill")

    return {
        "starting_capital": float(starting_capital),
        "ending_equity": float(ending),
        "net_profit": float(net_profit),
        "roi": float(roi),
        "cagr": cagr,
        "cagr_note": None if cagr is not None else "period too short to annualise",
        "span_days": span_days,
        "max_drawdown": max_drawdown(equity_points),
        "sharpe": sharpe,
        "sortino": sortino,
        "sharpe_note": None if sharpe is not None else "insufficient data",
        "n_trades": n_trades,
        "excluded_trades": dict(sorted(excluded.items())),
        "win_rate": len(wins) / n_trades if n_trades else None,
        "profit_factor": _profit_factor(wins, losses),
        "expectancy": float(sum(nets, Decimal(0)) / n_trades) if n_trades else None,
        "expectancy_ci": None,  # bootstrap CI arrives in M5 (PRD §15.2)
        "max_consecutive_losses": longest,
        "worst_trade": {"seq": worst["seq"], "net_pnl": float(Decimal(worst["net_pnl"]))}
        if worst
        else None,
        "holding_seconds_mean": statistics.fmean(holding) if holding else None,
        "holding_seconds_median": statistics.median(holding) if holding else None,
        "pnl_histogram": _histogram([float(n) for n in nets]),
        "exit_reasons": dict(sorted(Counter(t["exit_reason"] for t in executed).items())),
        "cost_attribution": {
            "gross_pnl": float(gross),
            "fees": float(fees),
            "slippage": float(slippage),
            "net_pnl": float(gross - fees - slippage),
        },
        "cost_ratio": cost_ratio,
        "low_sample": n_trades < LOW_SAMPLE_TRADES,
        "warnings": warnings,
        "definitions": DEFINITIONS,
    }
