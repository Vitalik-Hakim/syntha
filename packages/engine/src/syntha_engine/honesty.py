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

No I/O: the caller supplies the dataset and the buy-and-hold strategy document.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import numpy as np

from syntha_contracts.backtest import BacktestRequest, HonestyConfig
from syntha_contracts.enums import Side
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.data import Dataset, SettlementTable
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

_STREAM_BOOTSTRAP = 1
_STREAM_PERMUTATION = 2
_STREAM_RANDOM = 3
_STREAM_BUY_AND_HOLD = 4

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


def bootstrap(
    trades: list[dict[str, Any]],
    starting_capital: Decimal,
    iterations: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    executed = [t for t in trades if t["fill_status"] in ("FILLED", "PARTIAL")]
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
    sums = np.empty(iterations)
    chunk = max(1, 2_000_000 // n)  # bound memory at ~16 MB of indices per chunk
    for start in range(0, iterations, chunk):
        size = min(chunk, iterations - start)
        idx = rng.integers(0, n, size=(size, n))
        sums[start : start + size] = pnl[idx].sum(axis=1)
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
    perm = permutation_test(prep, result, real_roi, cfg.permutations, report)
    rand = random_baseline(prep, result, real_roi, cfg.random_baselines, report)
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
            "buy_and_hold_roi": None if bh is None else bh["roi"],
            "beats_buy_and_hold": None if bh is None else real_roi > bh["roi"],
            "roi_minus_buy_and_hold": None if bh is None else real_roi - bh["roi"],
            "evidence_of_edge": pct is not None and pct >= EDGE_PERCENTILE,
        },
    }
