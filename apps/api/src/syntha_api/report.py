"""Research reports (PRD M6): a run, or a comparison, as a self-contained Markdown document.

The report states the disclaimer, the assumptions and the honesty tests before any headline
figure, so that it can be shared without the application around it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from syntha_contracts.api import Comparison, RunDetail

DISCLAIMER = (
    "**SIMULATED RESULTS — NOT ACTUAL TRADING PERFORMANCE.** Backtested returns are hypothetical, "
    "rely on the execution assumptions shown below, and do not account for factors that affect "
    "real trading. Past performance does not indicate future results. This tool is for research "
    "and is not financial advice."
)


def _pct(x: Any, digits: int = 2, signed: bool = True) -> str:
    if not isinstance(x, int | float):
        return "—"
    return f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}"


def _usd(x: Any) -> str:
    if not isinstance(x, int | float):
        return "—"
    return f"{'−' if x < 0 else '+'}${abs(x):,.2f}"


def _num(x: Any, digits: int = 2) -> str:
    if isinstance(x, str):
        return "∞" if x == "inf" else x
    if not isinstance(x, int | float):
        return "—"
    return f"{x:,.{digits}f}" if isinstance(x, float) else f"{x:,}"


def _ci(pair: Any) -> str:
    return "—" if not pair else f"{_pct(pair[0])} to {_pct(pair[1])}"


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _config_lines(run: RunDetail) -> list[str]:
    c = run.config
    ex = c.execution
    s = c.strategy
    return [
        f"- **Strategy:** {s.name} (DSL hash `{s.dsl_hash()[:12]}`)",
        f"- **Markets:** {c.dataset.collection or 'all markets'}; universe side {s.universe.side.value}"
        f", categories {s.universe.categories or 'all'}, min volume as of bar {s.universe.min_volume_asof}"
        f", min time to scheduled end {s.universe.min_time_to_resolution_minutes} min",
        f"- **Execution:** {run.preset or 'CUSTOM'} — {ex.model.value}, slippage "
        f"{ex.slippage_ticks if ex.slippage_ticks is not None else ex.slippage_bps} "
        f"{'ticks' if ex.slippage_ticks is not None else 'bps'}, participation "
        f"{ex.liquidity_participation if ex.liquidity_participation is not None else 'unlimited'}, "
        f"partial fills {ex.partial_fill_policy.value}, fees {c.fees.mode.value}",
        f"- **Capital:** ${c.starting_capital:,}; decision lag {c.decision_lag_bars} bar(s) of "
        f"{c.bar_interval_minutes} min; seed {c.seed}; sizing {s.sizing.mode.value} {s.sizing.value}"
        f" (below-minimum orders: {s.sizing.min_order_policy})",
        f"- **Reproducibility:** run `{run.id}`, config hash `{run.config_hash[:16]}`, dataset version "
        f"`{run.dataset_version_id}`, engine {run.engine_version}",
    ]


def run_report(run: RunDetail) -> str:
    m: dict[str, Any] = run.metrics or {}
    dq: dict[str, Any] = run.data_quality or {}
    h: dict[str, Any] = run.honesty or {}
    s = h.get("summary") or {}
    boot = h.get("bootstrap") or {}
    perm = h.get("permutation") or {}
    rand = h.get("random_baseline") or {}
    bh = h.get("buy_and_hold") or {}
    costs = m.get("cost_attribution") or {}

    parts = [
        f"# Research report: {run.strategy_name} — {run.id}",
        "",
        DISCLAIMER,
        "",
        "## Verdict first",
        "",
    ]
    if h:
        parts += [
            f"- {s.get('headline')} {s.get('reading') or ''}".rstrip(),
            f"- ROI {_pct(m.get('roi'))}, 90% bootstrap CI {_ci(boot.get('roi_ci90'))}"
            + (" (fewer than 30 trades: unreliable)" if boot.get("low_sample") else ""),
            f"- Random-entry baseline: beat {_num(rand.get('percentile'), 0)}% of "
            f"{rand.get('iterations')} variants (median variant ROI "
            f"{_pct((rand.get('roi_quantiles') or {}).get('p50'))})",
            f"- Buy and hold on the same markets: ROI {_pct(bh.get('roi'))} "
            f"(90% CI {_ci((bh.get('bootstrap') or {}).get('roi_ci90'))}); strategy minus buy and hold "
            f"{_pct(s.get('roi_minus_buy_and_hold'))}",
        ]
    else:
        parts.append("- The honesty layer was not computed for this run (engine before 0.5.0).")
    parts += [
        "",
        *[f"> {w}" for w in m.get("warnings", [])],
        "",
        "## Configuration",
        "",
        *_config_lines(run),
    ]
    if run.group:
        parts += [
            "",
            "## Execution scenarios",
            "",
            _table(
                ["Preset", "Run", "Net P&L", "ROI", "ROI 90% CI", "Trades", "Beat permuted"],
                [
                    [
                        g.preset or "—",
                        g.id,
                        _usd(g.net_profit),
                        _pct(g.roi),
                        _ci(g.roi_ci90),
                        _num(g.n_trades),
                        f"{_num(g.permutation_percentile, 0)}%",
                    ]
                    for g in run.group
                ],
            ),
        ]
    parts += [
        "",
        "## Results",
        "",
        _table(
            ["Metric", "Value"],
            [
                ["Net P&L", _usd(m.get("net_profit"))],
                ["ROI", _pct(m.get("roi"))],
                [
                    "CAGR",
                    _pct(m.get("cagr")) if m.get("cagr") is not None else str(m.get("cagr_note")),
                ],
                [
                    "Max drawdown",
                    f"{_pct((m.get('max_drawdown') or {}).get('pct'), signed=False)} ({_usd((m.get('max_drawdown') or {}).get('usd'))})",
                ],
                ["Trades (executed)", _num(m.get("n_trades"))],
                ["Win rate", _pct(m.get("win_rate"), 1, signed=False)],
                ["Profit factor", _num(m.get("profit_factor"))],
                [
                    "Expectancy / trade",
                    f"{_usd(m.get('expectancy'))} (95% CI {_usd((boot.get('expectancy_ci95') or [None, None])[0])} to {_usd((boot.get('expectancy_ci95') or [None, None])[1])})",
                ],
                ["Sharpe (daily, indicative only)", _num(m.get("sharpe"))],
                [
                    "Signals → filled",
                    f"{_num(dq.get('signals_generated'))} → {_num(dq.get('signals_filled'))}",
                ],
                [
                    "Unexecutable / below minimum size",
                    f"{_num(dq.get('signals_unexecutable'))} / {_num(dq.get('signals_rejected_min_size'))}",
                ],
            ],
        ),
        "",
        "### Cost attribution",
        "",
        _table(
            ["", "USD"],
            [
                ["Gross P&L", _usd(costs.get("gross_pnl"))],
                ["Fees", _usd(-abs(costs.get("fees") or 0))],
                ["Slippage", _usd(-abs(costs.get("slippage") or 0))],
                ["**Net P&L**", f"**{_usd(costs.get('net_pnl'))}**"],
            ],
        ),
        f"\nCost ratio: {_num(m.get('cost_ratio'))}",
    ]
    breakdown = m.get("exit_reason_breakdown") or []
    if breakdown:
        parts += [
            "",
            "### Exit reasons",
            "",
            _table(
                ["Reason", "Trades", "Win rate", "Net P&L", "Mean return"],
                [
                    [
                        b["reason"],
                        _num(b["trades"]),
                        _pct(b["win_rate"], 0, signed=False),
                        _usd(b["net_pnl"]),
                        _pct(b["mean_return_pct"], 1),
                    ]
                    for b in breakdown
                ],
            ),
        ]
    if h:
        parts += [
            "",
            "## Is this better than chance?",
            "",
            f"- **Bootstrap** ({boot.get('iterations')} resamples of {boot.get('n_trades')} trades): "
            f"ROI 90% CI {_ci(boot.get('roi_ci90'))}; {_pct(boot.get('prob_loss'), 0, signed=False)} of "
            f"resamples lose money. Method: {boot.get('method')}.",
            f"- **Permutation test** ({perm.get('iterations')} variants): beat "
            f"{_num(perm.get('percentile'), 0)}%. Middle 90% of variant ROI "
            f"{_ci([(perm.get('roi_quantiles') or {}).get('p5'), (perm.get('roi_quantiles') or {}).get('p95')] if perm.get('roi_quantiles') else None)}. "
            f"{perm.get('episodes_without_alternative', '—')} of {perm.get('episodes', '—')} signals had no other "
            f"eligible market. Method: {perm.get('method')}.",
            f"- **Random entries** ({rand.get('iterations')} variants): beat {_num(rand.get('percentile'), 0)}%. "
            f"Method: {rand.get('method')}.",
            f"- **Buy and hold**: {bh.get('method')}. ROI {_pct(bh.get('roi'))}, {_num(bh.get('n_trades'))} trades.",
        ]
    funnel = dq.get("entry_funnel") or {}
    parts += [
        "",
        "## Data quality",
        "",
        f"- Coverage {(dq.get('coverage') or {}).get('start')} → {(dq.get('coverage') or {}).get('end')}; "
        f"{_num(dq.get('markets_with_usable_data'))} of {_num(dq.get('markets_selected'))} markets usable; "
        f"{_num(dq.get('bars_with_no_prints_pct'))}% of bars had no trade print",
        "- Entry funnel: " + ", ".join(f"{k} {v:,}" for k, v in funnel.items()),
        f"- Fee schedules applied: {', '.join(dq.get('fee_schedules_applied') or []) or 'none'}",
        *[f"- Bias warning: {w}" for w in dq.get("bias_warnings") or []],
        "",
        "### Assumptions",
        "",
        *[f"- {a}" for a in dq.get("assumptions") or []],
        "",
        "---",
        "",
        DISCLAIMER,
        "",
    ]
    return "\n".join(parts)


def _percentile(v: Any) -> str:
    return f"{_num(v, 0)}%"


def comparison_report(comparison: Comparison) -> str:
    runs = comparison.runs
    header = ["Metric", *[f"{r.id} ({r.preset or 'CUSTOM'})" for r in runs]]
    fmt: dict[str, Callable[[Any], str]] = {
        "usd": _usd,
        "pct": _pct,
        "count": _num,
        "ratio": _num,
        "percentile": _percentile,
    }

    def cell(metric_format: str, value: Any, delta: Any, first: bool) -> str:
        text = fmt.get(metric_format, str)(value)
        if first or delta is None or delta == 0:
            return text
        delta_text = f"{delta:+.2%}" if metric_format == "pct" else f"{delta:+,.2f}"
        return f"{text} ({delta_text})"

    parts = [
        f"# Comparison: {' vs '.join(r.id for r in runs)}",
        "",
        DISCLAIMER,
        "",
        "Deltas are against the first run.",
        "",
        "## What differs",
        "",
    ]
    if comparison.config_diff:
        parts.append(
            _table(
                ["Setting", *[r.id for r in runs]],
                [[f"`{d.path}`", *[f"`{v}`" for v in d.values]] for d in comparison.config_diff],
            )
        )
    else:
        parts.append(
            "The configurations are identical (only the data or engine version can differ)."
        )
    parts += [
        "",
        "## Results",
        "",
        _table(
            header,
            [
                [
                    m.label,
                    *[
                        cell(m.format, v, d, i == 0)
                        for i, (v, d) in enumerate(zip(m.values, m.deltas, strict=True))
                    ],
                ]
                for m in comparison.metrics
            ],
        ),
        "",
        "Anything under ~95% on the permutation test is not evidence of edge. A difference between "
        "two runs is only meaningful if it is larger than their confidence intervals.",
        "",
        "---",
        "",
        DISCLAIMER,
        "",
    ]
    return "\n".join(parts)
