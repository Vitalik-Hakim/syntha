"""Run a candidate grid over several collections, in memory, one worker per collection.

Nothing is persisted to backtest_runs (a sweep would bury the runs list); every result goes to
`data/research/<sweep>/<collection>.jsonl`, one line per configuration, resumable. Finalists are
persisted later with tools/research/run_candidates.py so they are browsable in the app.

Usage:
  uv run python tools/research/sweep.py --sweep discovery \
      --collections march-2026-liquid-400 april-2026-liquid-400 may-2026-liquid-400 \
      [--preset REALISTIC] [--capital 1000] [--permutations 100] [--workers 3] [--keys k1 k2]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from candidates import grid, grid2

RESEARCH_DIR = Path(os.environ.get("SYNTHA_DATA_DIR", "data")) / "research"


def _db_url() -> str:
    url = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _one(prep_cache: dict[Any, Any], request: Any, url: str) -> dict[str, Any]:
    """One configuration on one (already loaded) collection: the backtest plus its honesty tests."""
    from syntha_contracts.builtins import builtin_strategy
    from syntha_engine.honesty import compute_honesty
    from syntha_engine.runner import BacktestResult, prepare, simulate
    from syntha_worker.loader import load_dataset

    u = request.strategy.universe
    key = (request.dataset.collection, u.side.value, tuple(u.categories or ()))
    if key not in prep_cache:
        prep_cache[key] = load_dataset(url, request)
    loaded = prep_cache[key]
    t0 = time.perf_counter()
    prep = prepare(request, loaded.dataset, loaded.settlement)
    result = simulate(prep)
    assert isinstance(result, BacktestResult)
    t1 = time.perf_counter()
    h = compute_honesty(
        prep, result, buy_and_hold_dsl=builtin_strategy("buy_and_hold_to_resolution")
    )
    t2 = time.perf_counter()
    m, dq, s = result.metrics, result.data_quality, h["summary"]
    executed = [t for t in result.trades if t["fill_status"] in ("FILLED", "PARTIAL")]
    return {
        "roi": m["roi"],
        "net_profit": m["net_profit"],
        "n_trades": m["n_trades"],
        "win_rate": m["win_rate"],
        "max_drawdown_pct": m["max_drawdown"]["pct"],
        "cost_ratio": m["cost_ratio"],
        "cost_attribution": m["cost_attribution"],
        "exit_reasons": m["exit_reasons"],
        "signals": dq["signals_generated"],
        "filled": dq["signals_filled"],
        "unexecutable": dq["signals_unexecutable"],
        "rejected_min_size": dq["signals_rejected_min_size"],
        "execution_state": dq["execution_state"],
        "markets_usable": dq["markets_with_usable_data"],
        "trade_pnl": [float(t["net_pnl"]) for t in executed],
        "trade_sides": [t["side"] for t in executed],
        "roi_ci90": h["bootstrap"]["roi_ci90"],
        "permutation_percentile": s["permutation_percentile"],
        "permutation_variants": h["permutation"].get("roi_variants", []),
        "permutation_episodes_without_alternative": h["permutation"].get(
            "episodes_without_alternative"
        ),
        "random_percentile": s["random_baseline_percentile"],
        "buy_and_hold_roi": s["buy_and_hold_roi"],
        "seconds": {"simulate": round(t1 - t0, 2), "honesty": round(t2 - t1, 2)},
    }


def worker(
    collection: str, configs: list[dict[str, Any]], base: dict[str, Any], out_path: str
) -> tuple[str, int, int]:
    """All configurations on one collection, appending to the collection's JSONL."""
    from syntha_contracts.backtest import BacktestRequest

    url = _db_url()
    out = Path(out_path)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["key"])
    cache: dict[Any, Any] = {}
    ok = failed = 0
    with out.open("a") as fh:
        for cfg in configs:
            if cfg["key"] in done:
                continue
            request = BacktestRequest.model_validate(
                {k: v for k, v in base.items() if not k.startswith("_")}
                | {"strategy": cfg["dsl"], "dataset": {"collection": collection}}
            )
            row: dict[str, Any] = {
                "key": cfg["key"],
                "name": cfg["dsl"]["name"],
                "collection": collection,
                "preset": base.get("_preset"),
                "config_hash": request.config_hash(),
            }
            try:
                row.update(_one(cache, request, url))
                ok += 1
            except Exception as exc:  # a broken candidate must not stop the sweep
                row["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
                failed += 1
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(
                f"{collection} {cfg['key']}: "
                + (
                    f"ROI {row['roi']:+.2%} trades {row['n_trades']} perm {row['permutation_percentile']}"
                    if "roi" in row
                    else "ERROR"
                ),
                flush=True,
            )
    return collection, ok, failed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True, help="name of the output directory")
    ap.add_argument("--collections", nargs="+", required=True)
    ap.add_argument("--preset", default="REALISTIC")
    ap.add_argument("--capital", default="1000")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--permutations", type=int, default=100)
    ap.add_argument("--random-baselines", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--keys", nargs="*", help="run only these candidate keys")
    ap.add_argument("--extra", nargs="*", default=[], help="extra strategy JSON files to include")
    ap.add_argument("--grid", default="1", choices=["1", "2", "both"], help="which candidate grid")
    args = ap.parse_args()

    from syntha_contracts.backtest import BacktestRequest, ExecutionConfig, FeeConfig

    configs = {"1": grid(), "2": grid2(), "both": grid() + grid2()}[args.grid]
    for path in args.extra:
        dsl = json.loads(Path(path).read_text())
        configs.append({"key": Path(path).stem, "dsl": dsl})
    if args.keys:
        wanted = set(args.keys)
        configs = [c for c in configs if c["key"] in wanted]
    base = {
        "starting_capital": args.capital,
        "seed": args.seed,
        "execution": ExecutionConfig.preset(args.preset).model_dump(mode="json"),
        "fees": FeeConfig.preset(args.preset).model_dump(mode="json"),
        "honesty": {
            "bootstrap_iterations": args.bootstrap,
            "permutations": args.permutations,
            "random_baselines": args.random_baselines,
            "buy_and_hold": True,
        },
        "_preset": args.preset,
    }
    for cfg in configs:  # validate everything before spending an hour
        BacktestRequest.model_validate(
            {k: v for k, v in base.items() if not k.startswith("_")}
            | {"strategy": cfg["dsl"], "dataset": {"collection": args.collections[0]}}
        )
    out_dir = RESEARCH_DIR / args.sweep
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "grid.json").write_text(json.dumps({"base": base, "configs": configs}, indent=1))
    print(
        f"{len(configs)} configurations × {len(args.collections)} collections → {out_dir}",
        flush=True,
    )
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=min(args.workers, len(args.collections))) as pool:
        futures = [
            pool.submit(worker, c, configs, base, str(out_dir / f"{c}.jsonl"))
            for c in args.collections
        ]
        for f in futures:
            collection, ok, failed = f.result()
            print(f"{collection}: {ok} ok, {failed} failed", flush=True)
    print(f"done in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
