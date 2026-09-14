"""Run comparison (PRD §14 `POST /api/compare`) and research reports (PRD M6). Every number is read
from stored runs; deltas are computed here, never in the browser."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import numpy as np
import psycopg
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from syntha_api.deps import ConnDep, SettingsDep
from syntha_api.diff import diff
from syntha_api.lttb import lttb_indices
from syntha_api.report import comparison_report, run_report
from syntha_api.routes.backtests import get_backtest
from syntha_contracts.api import ComparedMetric, Comparison, DiffEntry, EquityLine, RunDetail

router = APIRouter(tags=["compare"])

EQUITY_POINTS = 600
# Paths that differ between any two runs by construction and say nothing about the setup.
_IGNORED_CONFIG = ()


class CompareRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=6)


def _get(d: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


Extractor = Callable[[RunDetail], float | str | None]

METRICS: list[tuple[str, str, str, str | None, Extractor]] = [
    ("net_profit", "Net P&L", "usd", "higher", lambda r: _get(r.metrics, "net_profit")),
    ("roi", "ROI", "pct", "higher", lambda r: _get(r.metrics, "roi")),
    (
        "roi_ci90_low",
        "ROI 90% CI low",
        "pct",
        "higher",
        lambda r: (_get(r.honesty, "bootstrap", "roi_ci90") or [None, None])[0],
    ),
    (
        "roi_ci90_high",
        "ROI 90% CI high",
        "pct",
        "higher",
        lambda r: (_get(r.honesty, "bootstrap", "roi_ci90") or [None, None])[1],
    ),
    (
        "permutation_percentile",
        "Beat permuted variants",
        "percentile",
        "higher",
        lambda r: _get(r.honesty, "summary", "permutation_percentile"),
    ),
    (
        "random_baseline_percentile",
        "Beat random entries",
        "percentile",
        "higher",
        lambda r: _get(r.honesty, "summary", "random_baseline_percentile"),
    ),
    (
        "buy_and_hold_roi",
        "Buy-and-hold ROI",
        "pct",
        None,
        lambda r: _get(r.honesty, "summary", "buy_and_hold_roi"),
    ),
    (
        "roi_minus_buy_and_hold",
        "Strategy − buy and hold",
        "pct",
        "higher",
        lambda r: _get(r.honesty, "summary", "roi_minus_buy_and_hold"),
    ),
    (
        "max_drawdown_pct",
        "Max drawdown",
        "pct",
        "lower",
        lambda r: _get(r.metrics, "max_drawdown", "pct"),
    ),
    ("n_trades", "Trades", "count", None, lambda r: _get(r.metrics, "n_trades")),
    ("win_rate", "Win rate", "pct", "higher", lambda r: _get(r.metrics, "win_rate")),
    (
        "profit_factor",
        "Profit factor",
        "ratio",
        "higher",
        lambda r: _get(r.metrics, "profit_factor"),
    ),
    ("expectancy", "Expectancy / trade", "usd", "higher", lambda r: _get(r.metrics, "expectancy")),
    (
        "gross_pnl",
        "Gross P&L",
        "usd",
        "higher",
        lambda r: _get(r.metrics, "cost_attribution", "gross_pnl"),
    ),
    ("fees", "Fees", "usd", "lower", lambda r: _get(r.metrics, "cost_attribution", "fees")),
    (
        "slippage",
        "Slippage",
        "usd",
        "lower",
        lambda r: _get(r.metrics, "cost_attribution", "slippage"),
    ),
    ("cost_ratio", "Cost ratio", "ratio", "lower", lambda r: _get(r.metrics, "cost_ratio")),
    (
        "signals_generated",
        "Signals",
        "count",
        None,
        lambda r: _get(r.data_quality, "signals_generated"),
    ),
    (
        "signals_filled",
        "Signals filled",
        "count",
        None,
        lambda r: _get(r.data_quality, "signals_filled"),
    ),
    (
        "signal_fill_rate",
        "Fill rate",
        "pct",
        "higher",
        lambda r: _get(r.data_quality, "signal_fill_rate"),
    ),
    (
        "signals_unexecutable",
        "Unexecutable",
        "count",
        "lower",
        lambda r: _get(r.data_quality, "signals_unexecutable"),
    ),
    (
        "signals_rejected_min_size",
        "Below minimum size",
        "count",
        "lower",
        lambda r: _get(r.data_quality, "signals_rejected_min_size"),
    ),
    (
        "sharpe",
        "Sharpe (daily, indicative)",
        "ratio",
        "higher",
        lambda r: _get(r.metrics, "sharpe"),
    ),
]


def _numeric(value: float | str | None) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _metrics(runs: list[RunDetail]) -> list[ComparedMetric]:
    rows = []
    for key, label, fmt, better, extract in METRICS:
        values = [extract(r) for r in runs]
        base = _numeric(values[0])
        deltas = [
            None if base is None or _numeric(v) is None else _numeric(v) - base  # type: ignore[operator]
            for v in values
        ]
        rows.append(
            ComparedMetric(
                key=key, label=label, format=fmt, better=better, values=values, deltas=deltas
            )
        )
    return rows


def _equity(conn: psycopg.Connection[Any], run_id: str) -> EquityLine:
    rows = conn.execute(
        "SELECT ts, equity FROM bt_equity WHERE run_id = %s ORDER BY ts", (run_id,)
    ).fetchall()
    ts = np.array([r[0].timestamp() for r in rows])
    eq = np.array([float(r[1]) for r in rows])
    if ts.size > EQUITY_POINTS:
        keep = lttb_indices(ts, eq, EQUITY_POINTS)
        ts, eq = ts[keep], eq[keep]
    return EquityLine(run_id=run_id, ts=[int(t) for t in ts], equity=[float(e) for e in eq])


def build_comparison(
    conn: psycopg.Connection[Any], settings: Any, run_ids: list[str]
) -> Comparison:
    if len(set(run_ids)) != len(run_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "run ids must be distinct")
    runs = [get_backtest(run_id, conn, settings) for run_id in run_ids]
    # The configuration, plus what else can change results for the same configuration.
    documents = [
        {
            **r.config.model_dump(mode="json"),
            "run": {"engine_version": r.engine_version, "dataset_version_id": r.dataset_version_id},
        }
        for r in runs
    ]
    return Comparison(
        runs=runs,
        config_diff=[DiffEntry(path=p, values=v) for p, v in diff(documents, _IGNORED_CONFIG)],
        metrics=_metrics(runs),
        equity=[_equity(conn, r.id) for r in runs],
    )


@router.post("/compare")
def compare(body: CompareRequest, conn: ConnDep, settings: SettingsDep) -> Comparison:
    return build_comparison(conn, settings, body.run_ids)


@router.get("/compare")
def compare_get(
    conn: ConnDep,
    settings: SettingsDep,
    run_ids: Annotated[list[str], Query(min_length=2, max_length=6)],
) -> Comparison:
    """The same comparison, addressable by URL (`?run_ids=A&run_ids=B`)."""
    return build_comparison(conn, settings, run_ids)


@router.get("/compare/report.md", response_class=PlainTextResponse)
def compare_report(
    conn: ConnDep,
    settings: SettingsDep,
    run_ids: Annotated[list[str], Query(min_length=2, max_length=6)],
) -> PlainTextResponse:
    text = comparison_report(build_comparison(conn, settings, run_ids))
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="compare-{"-vs-".join(run_ids)}.md"'
        },
    )


@router.get("/backtests/{run_id}/report.md", response_class=PlainTextResponse)
def backtest_report(run_id: str, conn: ConnDep, settings: SettingsDep) -> PlainTextResponse:
    run = get_backtest(run_id, conn, settings)
    if run.status.value != "DONE":
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is {run.status.value}")
    return PlainTextResponse(
        run_report(run),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-report.md"'},
    )
