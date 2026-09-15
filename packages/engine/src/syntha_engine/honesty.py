"""The statistical honesty layer (PRD §15): is this result distinguishable from luck?

Run with every backtest, seeded from the request's seed, so the answers are reproducible:

- Bootstrap (§15.2): resample the executed trades' net P&L with replacement. ROI is the resampled
  P&L sum over starting capital. This ignores path-dependent sizing (a different order of trades
  would have sized differently), which is the usual simplification; it is stated in the output.
- Permutation test (§15.3): keep every signal episode's start bar and retry window, and move it
  to a random market of the same category that was eligible at that bar. Simulate with the same
  constraints, sizing, execution, fees and exits. The strategy's percentile among these variants
  tests whether *which* market it picked mattered, holding *when* it traded fixed.
- Random-entry baseline (§15.3): the same number of episodes with the same retry windows, placed
  uniformly at random over every eligible (bar, market) pair.
- Buy and hold to resolution (§8.3): the built-in benchmark on the strategy's own universe.
- Cluster bootstrap (v3 E5): trades of one event, or one UTC day, are one draw; the wider of the
  two intervals is the headline. The primary hold-to-resolution metric is net return per dollar
  risked (Σ net / Σ notional), whose cluster SE gives the minimum detectable effect on a negative
  (MDE = 2.48 × SE: 80% power, one-sided 5%).
- Fee counterfactual (v3 E5): the same run under HISTORICAL and CURRENT fees; CURRENT decides.

No I/O: the caller supplies the dataset and the buy-and-hold strategy document.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from syntha_contracts.backtest import BacktestRequest, FeeConfig, HonestyConfig
from syntha_contracts.enums import FeeMode, Side
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.data import Dataset, SettlementTable
from syntha_engine.metrics.core import executed_trades
from syntha_engine.runner import (
    BacktestResult,
    ForcedSignals,
    LeanOutcome,
    Preparation,
    prepare,
    simulate,
)

EDGE_PERCENTILE = 95.0  # PRD §15.3: anything under ~95 is not evidence of edge
LOW_SAMPLE_TRADES = 30
MDE_MULTIPLIER = 2.48  # v3 E5: z(0.95) + z(0.80) = 1.645 + 0.842, one-sided 5%, 80% power
CLUSTER_KEYS = ("event", "day")
DECIDING_FEE_MODE = "CURRENT"  # v3 E5: an edge that only existed fee-free is not an edge

_STREAM_BOOTSTRAP = 1
_STREAM_PERMUTATION = 2
_STREAM_RANDOM = 3
_STREAM_BUY_AND_HOLD = 4
_STREAM_CLUSTER = 5

Progress = Callable[[str, float], None]


def _rng(request: BacktestRequest, stream: int, i: int = 0) -> np.random.Generator:
    return np.random.default_rng([request.seed & 0xFFFFFFFF, 0x5EED, stream, i])


def _quantiles(values: np.ndarray) -> dict[str, float] | None:
    if values.size == 0:
        return None
    p = np.percentile(values, [5, 25, 50, 75, 95])
    return {
        "p5": float(p[0]),
        "p25": float(p[1]),
        "p50": float(p[2]),
        "p75": float(p[3]),
        "p95": float(p[4]),
    }


def _histogram(values: np.ndarray, bins: int = 24) -> dict[str, list[float] | list[int]]:
    if values.size == 0:
        return {"edges": [], "counts": []}
    lo, hi = float(values.min()), float(values.max())
    if lo == hi:
        lo, hi = lo - 1e-9, hi + 1e-9
    counts, edges = np.histogram(values, bins=bins, range=(lo, hi))
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def share_beaten(real: float, values: np.ndarray) -> float | None:
    """Percent of variants the real result beat; ties count half."""
    if values.size == 0:
        return None
    return float(100 * (np.sum(values < real) + 0.5 * np.sum(values == real)) / values.size)


# ------------------------------------------------------------------ bootstrap (§15.2)


def _resampled_sums(values: np.ndarray, iterations: int, rng: np.random.Generator) -> np.ndarray:
    """Σ of `n` draws with replacement from `values` (rows), `iterations` times, in chunks so the
    index array stays around 16 MB. Every value column is summed with the same draws."""
    n = values.shape[0]
    sums = np.empty((iterations, values.shape[1]))
    chunk = max(1, 2_000_000 // n)
    for start in range(0, iterations, chunk):
        size = min(chunk, iterations - start)
        idx = rng.integers(0, n, size=(size, n))
        sums[start : start + size] = values[idx].sum(axis=1)
    return sums


def bootstrap(
    trades: list[dict[str, Any]],
    starting_capital: Decimal,
    iterations: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    executed = executed_trades(trades)
    n = len(executed)
    base: dict[str, Any] = {
        "iterations": iterations,
        "n_trades": n,
        "low_sample": n < LOW_SAMPLE_TRADES,
        "method": "resample executed trades' net P&L with replacement; ROI = Σ net P&L / starting "
        "capital (path-dependent sizing ignored)",
    }
    if iterations == 0 or n == 0:
        return {
            **base,
            "roi_ci90": None,
            "expectancy_ci95": None,
            "prob_loss": None,
            "note": "disabled" if iterations == 0 else "no executed trades to resample",
        }
    pnl = np.array([float(Decimal(t["net_pnl"])) for t in executed])
    capital = float(starting_capital)
    sums = _resampled_sums(pnl[:, None], iterations, rng)[:, 0]
    roi = sums / capital
    means = sums / n
    return {
        **base,
        "roi": float(pnl.sum() / capital),
        "roi_ci90": [float(np.percentile(roi, 5)), float(np.percentile(roi, 95))],
        "expectancy": float(pnl.mean()),
        "expectancy_ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
        "prob_loss": float(np.mean(roi <= 0)),
        "note": None,
    }


# ------------------------------------------------------------------ cluster bootstrap (v3 E5)


def cluster_labels(trades: Sequence[dict[str, Any]], dataset: Dataset) -> dict[str, list[str]]:
    """Per executed trade, its event (falling back to the market) and its UTC entry day."""
    event: list[str] = []
    day: list[str] = []
    for t in trades:
        cid = str(t["condition_id"])
        if cid.startswith("event:"):
            ev = cid.removeprefix("event:")
        else:
            terms = dataset.markets.get(cid)
            ev = (terms.event_id if terms is not None and terms.event_id else None) or cid
        event.append(ev)
        day.append(str(t["entry_ts"])[:10])
    return {"event": event, "day": day}


def cluster_bootstrap(
    trades: Sequence[dict[str, Any]],
    labels: Sequence[str],
    starting_capital: Decimal,
    iterations: int,
    rng: np.random.Generator,
    key: str,
) -> dict[str, Any]:
    """Resample clusters of executed trades with replacement (clusters in order of first
    appearance, so one trade per cluster reproduces the trade-level bootstrap draw for draw).
    Statistics: ROI = Σ net / capital and return per dollar = Σ net / Σ notional."""
    executed = executed_trades(trades)
    order: dict[str, int] = {}
    codes = np.array([order.setdefault(lbl, len(order)) for lbl in labels], dtype=np.int64)
    n_clusters = len(order)
    base: dict[str, Any] = {"key": key, "n_trades": len(executed), "n_clusters": n_clusters}
    if iterations == 0 or not executed:
        return {
            **base,
            "roi_ci90": None,
            "return_per_dollar_ci90": None,
            "se_return_per_dollar": None,
            "se_roi": None,
            "mde_80_return_per_dollar": None,
            "mde_80_roi": None,
            "note": "disabled" if iterations == 0 else "no executed trades to resample",
        }
    pnl = np.array([float(Decimal(t["net_pnl"])) for t in executed])
    notional = np.array([float(Decimal(t["notional"])) for t in executed])
    per_cluster = np.zeros((n_clusters, 2))
    np.add.at(per_cluster[:, 0], codes, pnl)
    np.add.at(per_cluster[:, 1], codes, notional)
    sums = _resampled_sums(per_cluster, iterations, rng)
    roi = sums[:, 0] / float(starting_capital)
    with np.errstate(divide="ignore", invalid="ignore"):
        rpd = np.where(sums[:, 1] > 0, sums[:, 0] / sums[:, 1], 0.0)
    se_rpd = float(rpd.std(ddof=1)) if iterations > 1 else 0.0
    se_roi = float(roi.std(ddof=1)) if iterations > 1 else 0.0
    return {
        **base,
        "roi": float(pnl.sum() / float(starting_capital)),
        "roi_ci90": [float(np.percentile(roi, 5)), float(np.percentile(roi, 95))],
        "return_per_dollar": float(pnl.sum() / notional.sum()) if notional.sum() > 0 else None,
        "return_per_dollar_ci90": [float(np.percentile(rpd, 5)), float(np.percentile(rpd, 95))],
        "se_return_per_dollar": se_rpd,
        "se_roi": se_roi,
        "mde_80_return_per_dollar": MDE_MULTIPLIER * se_rpd,
        "mde_80_roi": MDE_MULTIPLIER * se_roi,
        "note": "one cluster: the interval is degenerate" if n_clusters == 1 else None,
    }


def _width(ci: list[float] | None) -> float:
    return -1.0 if ci is None else ci[1] - ci[0]


def cluster_report(
    prep: Preparation, trades: Sequence[dict[str, Any]], iterations: int
) -> dict[str, Any]:
    """Both clusterings; the wider return-per-dollar interval is the headline (v3 E5)."""
    executed = executed_trades(trades)
    labels = cluster_labels(executed, prep.dataset)
    by_key = {
        key: cluster_bootstrap(
            executed,
            labels[key],
            prep.request.starting_capital,
            iterations,
            _rng(prep.request, _STREAM_CLUSTER, i),
            key,
        )
        for i, key in enumerate(CLUSTER_KEYS)
    }
    headline_key = max(CLUSTER_KEYS, key=lambda k: _width(by_key[k]["return_per_dollar_ci90"]))
    head = by_key[headline_key]
    return {
        "iterations": iterations,
        "method": "resample clusters of executed trades with replacement; trades of the same "
        "event (or the same UTC entry day) are one draw; the wider interval is the headline",
        "by_key": by_key,
        "headline_key": headline_key,
        "roi_ci90": head["roi_ci90"],
        "return_per_dollar": head.get("return_per_dollar"),
        "return_per_dollar_ci90": head["return_per_dollar_ci90"],
        "mde_80": head["mde_80_return_per_dollar"],
        "mde_80_roi": head["mde_80_roi"],
        "n_clusters": {k: by_key[k]["n_clusters"] for k in CLUSTER_KEYS},
    }


# ------------------------------------------------------------------ fee counterfactual (v3 E5)


def fee_counterfactual(prep: Preparation, result: BacktestResult) -> dict[str, Any]:
    """The run under HISTORICAL fees (what happened) and CURRENT fees (what it costs today), on
    the same preparation. Decisions use CURRENT."""
    run_mode = prep.request.fees.mode.value
    out: dict[str, Any] = {
        "decides": DECIDING_FEE_MODE,
        "run_mode": run_mode,
        "method": "the same signals, sizing and execution re-simulated with the other fee mode; "
        "HISTORICAL uses the schedule at each fill, CURRENT today's schedule for every fill",
    }
    for mode in (FeeMode.HISTORICAL, FeeMode.CURRENT):
        if mode.value == run_mode:
            r = result
        else:
            req = prep.request.model_copy(
                update={
                    "fees": FeeConfig(mode=mode),
                    "honesty": HonestyConfig(
                        bootstrap_iterations=0,
                        permutations=0,
                        random_baselines=0,
                        buy_and_hold=False,
                    ),
                }
            )
            outcome = simulate(dataclasses.replace(prep, request=req), near_misses=False)
            assert isinstance(outcome, BacktestResult)
            r = outcome
        m = r.metrics
        out[mode.value] = {
            "roi": m["roi"],
            "net_profit": m["net_profit"],
            "return_per_dollar": m["return_per_dollar"]["pooled"],
            "fees": m["cost_attribution"]["fees"],
            "n_trades": m["n_trades"],
        }
    hist, cur = out["HISTORICAL"], out["CURRENT"]
    out["roi_difference"] = cur["roi"] - hist["roi"]
    out["positive_only_under_historical"] = bool(hist["roi"] > 0 >= cur["roi"])
    return out


def honesty_metrics(honesty: dict[str, Any]) -> dict[str, Any]:
    """The E5 fields every stored `metrics` carries next to the run's own metrics."""
    s = honesty["summary"]
    return {
        "mde_80": s["mde_80"],
        "cluster_ci90": s["cluster_ci90"],
        "cluster_key": s["cluster_key"],
        "return_per_dollar_ci90": s["return_per_dollar_ci90"],
        "fee_counterfactual": honesty["fee_counterfactual"],
    }


# ------------------------------------------------------------------ forced schedules


def _schedule(prep: Preparation, episodes: list[tuple[int, int, int]], label: str) -> ForcedSignals:
    """Candidate bars for (start bar, window length in bars, token index) episodes: every bar of
    the window at which the token is eligible, like a real episode's retries."""
    names = prep.token_names
    ks: list[np.ndarray] = []
    toks: list[np.ndarray] = []
    for start, length, ti in episodes:
        p = prep.prepared[names[ti]]
        assert p.gate is not None
        i0 = start - p.frame.k0
        i1 = min(i0 + length, p.frame.n - 1)
        if i0 < 0 or i0 > i1:
            continue
        local = np.flatnonzero(p.gate[i0 : i1 + 1]) + i0
        ks.append(local + p.frame.k0)
        toks.append(np.full(local.size, ti, dtype=np.int64))
    if not ks:
        return ForcedSignals(np.empty(0, np.int64), np.empty(0, np.int32), label)
    width = max(len(names), 1)
    keys = np.unique(np.concatenate(ks) * width + np.concatenate(toks))  # bar order, then token
    return ForcedSignals(keys // width, (keys % width).astype(np.int32), label)


def _real_episodes(prep: Preparation, result: BacktestResult) -> list[tuple[int, int, int]]:
    index = {name: i for i, name in enumerate(prep.token_names)}
    return [
        (start, last - start, index[tok]) for start, last, tok in result.episodes if tok in index
    ]


def _lean(prep: Preparation, forced: ForcedSignals) -> LeanOutcome:
    outcome = simulate(prep, forced=forced)
    assert isinstance(outcome, LeanOutcome)
    return outcome


def _distribution(
    kind: str, real_roi: float, outcomes: list[LeanOutcome], extra: dict[str, Any]
) -> dict[str, Any]:
    rois = np.array([o.roi for o in outcomes])
    filled = np.array([o.signals_filled for o in outcomes])
    return {
        "kind": kind,
        "iterations": len(outcomes),
        "roi_real": real_roi,
        "percentile": share_beaten(real_roi, rois),
        "roi_quantiles": _quantiles(rois),
        "roi_mean": float(rois.mean()) if rois.size else None,
        "histogram": _histogram(rois),
        # every variant, so a sweep can ask what the best of N configurations looks like by
        # chance (PRD §15.4)
        "roi_variants": [round(float(r), 6) for r in rois],
        "signals_filled_median": float(np.median(filled)) if filled.size else None,
        **extra,
    }


# ------------------------------------------------------------------ permutation test (§15.3)


def permutation_test(
    prep: Preparation,
    result: BacktestResult,
    real_roi: float,
    iterations: int,
    progress: Progress,
) -> dict[str, Any]:
    episodes = _real_episodes(prep, result)
    method = (
        "each signal episode keeps its start bar and retry window and moves to a random market of "
        "the same category eligible at that bar"
    )
    if iterations == 0 or not episodes:
        note = "disabled" if iterations == 0 else "the strategy produced no signals to permute"
        return {**_distribution("permutation", real_roi, [], {}), "method": method, "note": note}

    dataset = prep.dataset
    names = prep.token_names
    category = [
        (dataset.markets[dataset.tokens[name].condition_id].category or "unknown").lower()
        for name in names
    ]
    starts = np.array([e[0] for e in episodes], dtype=np.int64)
    alternatives: list[np.ndarray] = [np.empty(0, np.int64)] * len(episodes)
    for cat in sorted(set(category[e[2]] for e in episodes)):
        rows = np.array([j for j, e in enumerate(episodes) if category[e[2]] == cat])
        members = np.array([i for i, c in enumerate(category) if c == cat], dtype=np.int64)
        eligible = np.zeros((rows.size, members.size), dtype=bool)
        for col, ti in enumerate(members):
            p = prep.prepared[names[ti]]
            assert p.gate is not None
            local = starts[rows] - p.frame.k0
            inside = (local >= 0) & (local < p.frame.n)
            eligible[inside, col] = p.gate[local[inside]]
        for r, j in enumerate(rows):
            alternatives[j] = members[eligible[r]]
    sizes = np.array([a.size for a in alternatives])
    flat = np.concatenate(alternatives)
    offsets = np.concatenate(([0], np.cumsum(sizes)[:-1]))

    outcomes = []
    for i in range(iterations):
        progress("permutation", i / iterations)
        rng = _rng(prep.request, _STREAM_PERMUTATION, i)
        picks = flat[offsets + np.floor(rng.random(len(episodes)) * sizes).astype(np.int64)]
        moved = [(e[0], e[1], int(t)) for e, t in zip(episodes, picks, strict=True)]
        outcomes.append(_lean(prep, _schedule(prep, moved, "PERMUTATION")))
    return {
        **_distribution(
            "permutation",
            real_roi,
            outcomes,
            {
                "episodes": len(episodes),
                "episodes_without_alternative": int(np.sum(sizes <= 1)),
                "median_alternatives": float(np.median(sizes)),
            },
        ),
        "method": method,
        "note": None,
    }


# ------------------------------------------------------------------ random-entry baseline (§15.3)


def random_baseline(
    prep: Preparation,
    result: BacktestResult,
    real_roi: float,
    iterations: int,
    progress: Progress,
) -> dict[str, Any]:
    episodes = _real_episodes(prep, result)
    names = prep.token_names
    gates = [prep.prepared[name].gate for name in names]
    counts = np.array([0 if g is None else int(g.sum()) for g in gates], dtype=np.int64)
    total = int(counts.sum())
    method = (
        "the same number of signal episodes, with the strategy's retry windows, placed uniformly "
        "at random over every eligible (bar, market)"
    )
    if iterations == 0 or not episodes or total == 0:
        note = (
            "disabled"
            if iterations == 0
            else "the strategy produced no signals"
            if not episodes
            else "no eligible bars"
        )
        return {
            **_distribution("random_baseline", real_roi, [], {}),
            "method": method,
            "note": note,
        }

    cum = np.cumsum(counts)
    lengths = np.array([e[1] for e in episodes], dtype=np.int64)
    outcomes = []
    for i in range(iterations):
        progress("random_baseline", i / iterations)
        rng = _rng(prep.request, _STREAM_RANDOM, i)
        draws = np.sort(rng.integers(0, total, size=len(episodes)))
        token_of = np.searchsorted(cum, draws, side="right")
        windows = rng.permutation(lengths)
        placed: list[tuple[int, int, int]] = []
        for ti in np.unique(token_of):
            p = prep.prepared[names[ti]]
            assert p.gate is not None
            eligible = np.flatnonzero(p.gate)
            mine = np.flatnonzero(token_of == ti)
            local = draws[mine] - (cum[ti] - counts[ti])
            for j, bar in zip(mine, eligible[local], strict=True):
                placed.append((int(p.frame.k0 + bar), int(windows[j]), int(ti)))
        outcomes.append(_lean(prep, _schedule(prep, placed, "RANDOM_ENTRY")))
    return {
        **_distribution("random_baseline", real_roi, outcomes, {"episodes": len(episodes)}),
        "method": method,
        "note": None,
    }


# ------------------------------------------------------------------ buy and hold (§8.3)


def buy_and_hold(
    request: BacktestRequest,
    dataset: Dataset,
    settlement: SettlementTable,
    dsl: StrategyDSL,
    rng: np.random.Generator,
) -> dict[str, Any]:
    strategy = dsl.model_copy(update={"universe": request.strategy.universe})
    req = request.model_copy(
        update={
            "strategy": strategy,
            "honesty": HonestyConfig(
                bootstrap_iterations=0, permutations=0, random_baselines=0, buy_and_hold=False
            ),
        }
    )
    result = simulate(prepare(req, dataset, settlement), near_misses=False)
    assert isinstance(result, BacktestResult)
    m, dq = result.metrics, result.data_quality
    daily: dict[str, float] = {}
    for row in result.equity:
        daily[row["ts"][:10]] = float(row["equity"])  # last equity of each UTC day
    return {
        "strategy": dsl.name,
        "method": "the built-in buy-and-hold strategy on this strategy's universe, with the same "
        "execution, fees and starting capital; its own sizing and constraints"
        + (
            " (universe EITHER: it buys whichever side of each market is listed first, a coin-flip side)"
            if request.strategy.universe.side == Side.EITHER
            else ""
        ),
        "sizing": strategy.sizing.model_dump(mode="json"),
        "constraints": strategy.constraints.model_dump(mode="json"),
        "roi": m["roi"],
        "net_profit": m["net_profit"],
        "n_trades": m["n_trades"],
        "win_rate": m["win_rate"],
        "max_drawdown_pct": m["max_drawdown"]["pct"],
        "cost_attribution": m["cost_attribution"],
        "exit_reasons": m["exit_reasons"],
        "signals_generated": dq["signals_generated"],
        "signals_filled": dq["signals_filled"],
        "execution_state": dq["execution_state"],
        # a benchmark is noisy too: its interval says how much of its return is luck
        "bootstrap": bootstrap(
            result.trades, request.starting_capital, request.honesty.bootstrap_iterations, rng
        ),
        "equity_daily": [[day, value] for day, value in sorted(daily.items())],
    }


# ------------------------------------------------------------------ everything


def compute_honesty(
    prep: Preparation,
    result: BacktestResult,
    *,
    buy_and_hold_dsl: StrategyDSL | None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    request = prep.request
    cfg = request.honesty
    report = progress or (lambda phase, fraction: None)
    real_roi = float(result.metrics["roi"])

    report("bootstrap", 0.0)
    boot = bootstrap(
        result.trades,
        request.starting_capital,
        cfg.bootstrap_iterations,
        _rng(request, _STREAM_BOOTSTRAP),
    )
    clusters = cluster_report(prep, result.trades, cfg.bootstrap_iterations)
    perm = permutation_test(prep, result, real_roi, cfg.permutations, report)
    rand = random_baseline(prep, result, real_roi, cfg.random_baselines, report)
    report("fee_counterfactual", 0.0)
    fees = fee_counterfactual(prep, result)
    bh = None
    if cfg.buy_and_hold and buy_and_hold_dsl is not None:
        report("buy_and_hold", 0.0)
        bh = buy_and_hold(
            request,
            prep.dataset,
            prep.settlement,
            buy_and_hold_dsl,
            _rng(request, _STREAM_BUY_AND_HOLD),
        )

    pct = perm["percentile"]
    if pct is None:
        headline = "No permutation test: " + (perm.get("note") or "not run")
        reading = None
    else:
        headline = f"Your strategy beat {pct:.0f}% of permuted variants."
        reading = (
            "Anything under ~95% is not evidence of edge."
            if pct < EDGE_PERCENTILE
            else "At or above 95%: consistent with an edge on this data slice. Check it out of "
            "sample before believing it."
        )
    return {
        "seed": request.seed,
        "config": cfg.model_dump(mode="json"),
        "bootstrap": boot,
        "cluster_bootstrap": clusters,
        "fee_counterfactual": fees,
        "permutation": perm,
        "random_baseline": rand,
        "buy_and_hold": bh,
        "summary": {
            "headline": headline,
            "reading": reading,
            "permutation_percentile": pct,
            "random_baseline_percentile": rand["percentile"],
            "roi": real_roi,
            "roi_ci90": boot["roi_ci90"],
            # v3 E5: the headline interval is the wider cluster interval, never the trade-level one
            "cluster_ci90": clusters["roi_ci90"],
            "cluster_key": clusters["headline_key"],
            "return_per_dollar": result.metrics["return_per_dollar"]["pooled"],
            "return_per_dollar_ci90": clusters["return_per_dollar_ci90"],
            "mde_80": clusters["mde_80"],
            "roi_current_fees": fees["CURRENT"]["roi"],
            "roi_historical_fees": fees["HISTORICAL"]["roi"],
            "deciding_fee_mode": DECIDING_FEE_MODE,
            "buy_and_hold_roi": None if bh is None else bh["roi"],
            "beats_buy_and_hold": None if bh is None else real_roi > bh["roi"],
            "roi_minus_buy_and_hold": None if bh is None else real_roi - bh["roi"],
            "evidence_of_edge": pct is not None and pct >= EDGE_PERCENTILE,
        },
    }
