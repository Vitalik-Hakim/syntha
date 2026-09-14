"""Run strategy documents against a collection as ordinary, persisted backtests (so they can be
inspected, compared and reported on in the app) and print the honesty verdict for each.

Every configuration tried is counted and printed: with N candidates, the best of N looks better
than it is (PRD §15.4). Screen on a discovery collection; confirm unchanged on a holdout.

Usage:
  uv run python tools/research/run_candidates.py --collection july-2026-liquid-400 \
      examples/research/*.json [--preset REALISTIC] [--capital 1000] [--all-presets]
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

import psycopg

from syntha_contracts.backtest import PRESETS, BacktestRequest
from syntha_worker.loader import LoadedDataset, load_dataset
from syntha_worker.runs import create_run, execute_run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--collection", required=True)
    ap.add_argument("--preset", default="REALISTIC", choices=PRESETS)
    ap.add_argument("--all-presets", action="store_true")
    ap.add_argument("--capital", default="1000")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    url = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    url = url.replace("postgresql+psycopg://", "postgresql://")

    results = []
    cache: dict[tuple[object, ...], LoadedDataset] = {}
    for path in args.files:
        dsl = json.loads(Path(path).read_text())
        base = BacktestRequest.model_validate(
            {
                "strategy": dsl,
                "dataset": {"collection": args.collection},
                "starting_capital": args.capital,
                "seed": args.seed,
            }
        )
        presets = PRESETS if args.all_presets else (args.preset,)
        group = uuid.uuid4() if args.all_presets else None
        for preset in presets:
            with psycopg.connect(url) as conn:
                run_id = create_run(conn, base.with_preset(preset), group)
                conn.commit()
            request = base.with_preset(preset)
            u = request.strategy.universe
            key = (args.collection, u.side, tuple(u.categories or ()))
            if key not in cache:
                cache[key] = load_dataset(url, request)
            status = execute_run(run_id, url, loaded=cache[key])
            with psycopg.connect(url) as conn:
                row = conn.execute(
                    "SELECT metrics, data_quality, honesty FROM backtest_runs WHERE id = %s",
                    (run_id,),
                ).fetchone()
            assert row is not None
            m, dq, h = row[0] or {}, row[1] or {}, row[2] or {}
            s = h.get("summary") or {}
            results.append(
                {
                    "run_id": run_id,
                    "status": status.value,
                    "strategy": dsl["name"],
                    "preset": preset,
                    "trades": m.get("n_trades"),
                    "roi": m.get("roi"),
                    "roi_ci90": s.get("roi_ci90"),
                    "perm": s.get("permutation_percentile"),
                    "random": s.get("random_baseline_percentile"),
                    "bh_roi": s.get("buy_and_hold_roi"),
                    "signals": dq.get("signals_generated"),
                    "filled": dq.get("signals_filled"),
                    "cost_ratio": m.get("cost_ratio"),
                }
            )
            print(json.dumps(results[-1]), flush=True)

    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x:+.2%}"

    print(
        f"\n{len(results)} configurations run on {args.collection}. The best of these is optimistic by construction."
    )
    print(
        f"{'run':18s} {'strategy':32s} {'preset':12s} {'trades':>6s} {'ROI':>8s} {'90% CI':>19s} {'perm':>5s} {'rand':>5s} {'B&H':>8s}"
    )
    for r in sorted(results, key=lambda r: -(r["roi"] or -9)):
        ci = r["roi_ci90"]
        ci_text = "—" if not ci else f"{pct(ci[0])}..{pct(ci[1])}"
        print(
            f"{r['run_id']:18s} {r['strategy'][:32]:32s} {r['preset']:12s} {r['trades'] or 0:>6d} "
            f"{pct(r['roi']):>8s} {ci_text:>19s} {('—' if r['perm'] is None else f'{r["perm"]:.0f}%'):>5s} "
            f"{('—' if r['random'] is None else f'{r["random"]:.0f}%'):>5s} {pct(r['bh_roi']):>8s}"
        )


if __name__ == "__main__":
    main()
