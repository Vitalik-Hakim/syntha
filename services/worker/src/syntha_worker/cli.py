"""`strategy-lab` — run backtests from the command line (PRD M2). No UI.

    strategy-lab run CONFIG.json [--fixture DIR] [--preset REALISTIC] [--out DIR]
    strategy-lab validate STRATEGY.json [--bar-interval 1]
    strategy-lab builtins [NAME]

CONFIG.json is a BacktestRequest (schema: packages/contracts/backtest-request.schema.json).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from syntha_contracts.backtest import BacktestRequest, ExecutionConfig, FeeConfig
from syntha_contracts.builtins import BUILTIN_STRATEGY_FILES, builtin_strategy
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.runner import BacktestResult, run
from syntha_engine.serialize import canonical_json, result_document
from syntha_engine.strategy.compiler import compile_strategy
from syntha_engine.strategy.features import FeatureError

DISCLAIMER = (
    "SIMULATED RESULTS — NOT ACTUAL TRADING PERFORMANCE. Backtested returns are hypothetical, rely on "
    "the execution assumptions shown below, and do not account for factors that affect real trading. "
    "Past performance does not indicate future results. This tool is for research and is not financial advice."
)

TRADE_COLUMNS = [
    "seq",
    "fill_status",
    "condition_id",
    "clob_token_id",
    "side",
    "signal_ts",
    "entry_ts",
    "entry_ref_price",
    "entry_price",
    "shares",
    "notional",
    "entry_fee",
    "exit_ts",
    "exit_price",
    "exit_fee",
    "exit_reason",
    "gross_pnl",
    "slippage_cost",
    "fees_total",
    "net_pnl",
    "return_pct",
    "holding_seconds",
]


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_outputs(out: Path, request: BacktestRequest, result: BacktestResult) -> None:
    out.mkdir(parents=True, exist_ok=True)
    doc = result_document(result)
    (out / "request.json").write_text(json.dumps(request.model_dump(mode="json"), indent=2) + "\n")
    for name in ("manifest", "metrics", "data_quality"):
        (out / f"{name}.json").write_text(json.dumps(doc[name], indent=2, default=str) + "\n")
    (out / "trades.jsonl").write_text("".join(canonical_json(t) + "\n" for t in result.trades))
    _write_csv(out / "trades.csv", result.trades, TRADE_COLUMNS)
    _write_csv(
        out / "equity.csv", result.equity, list(result.equity[0]) if result.equity else ["ts"]
    )
    _write_csv(out / "ledger.csv", result.ledger, ["ts", "type", "amount", "running_cash", "ref"])


def fmt(x: Any) -> str:
    if x is None:
        return "—"
    return f"{x:+.2%}" if isinstance(x, float) else str(x)


def _summary(request: BacktestRequest, result: BacktestResult, elapsed: float, out: Path) -> str:
    m, dq = result.metrics, result.data_quality
    win_rate = "—" if m["win_rate"] is None else f"{m['win_rate']:.1%}"
    lines = [DISCLAIMER, ""]
    lines += [f"!! {w}" for w in m["warnings"]]
    lines += [
        "",
        f"strategy        {result.manifest['strategy']}",
        f"execution       {dq['execution_model']} · lag {dq['decision_lag_bars']} bar(s) · fees {dq['fee_mode']}",
        f"coverage        {dq['coverage']['start']} → {dq['coverage']['end']} ({dq['bar_interval_minutes']}m bars)",
        f"markets         {dq['markets_with_usable_data']} usable of {dq['markets_selected']} selected",
        f"signals         {dq['signals_generated']} generated · {dq['signals_filled']} filled · "
        f"{dq['signals_unexecutable']} unexecutable {dq['signals_unexecutable_reasons']} · "
        f"{dq['signals_rejected_min_size']} rejected for minimum size",
        f"entry funnel    {dq['entry_funnel']}",
        "",
        f"starting        ${m['starting_capital']:.2f}",
        f"ending equity   ${m['ending_equity']:.2f}",
        f"net profit      ${m['net_profit']:+.2f}  (ROI {fmt(m['roi'])})",
        f"max drawdown    ${m['max_drawdown']['usd']:.2f}  ({m['max_drawdown']['pct']:.2%})",
        f"trades          {m['n_trades']} · win rate {win_rate} · profit factor {m['profit_factor']}",
        f"costs           gross {m['cost_attribution']['gross_pnl']:+.2f} · fees -{m['cost_attribution']['fees']:.2f}"
        f" · slippage -{m['cost_attribution']['slippage']:.2f} · net {m['cost_attribution']['net_pnl']:+.2f}",
        f"exit reasons    {m['exit_reasons']}",
        "",
        f"config hash     {result.manifest['config_hash']}",
        f"dataset version {result.manifest['dataset_version_id']}",
        f"runtime         {elapsed:.1f}s · outputs in {out}",
    ]
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.config).read_text())
    if args.preset:
        spec["execution"] = ExecutionConfig.preset(args.preset).model_dump(mode="json")
        spec["fees"] = FeeConfig.preset(args.preset).model_dump(mode="json")
    try:
        request = BacktestRequest.model_validate(spec)
        compile_strategy(request.strategy, request.bar_interval_minutes)
    except (ValidationError, FeatureError) as exc:
        print(f"invalid config: {exc}", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    if args.fixture:
        from syntha_worker.fixtures import load_fixture

        fx = load_fixture(Path(args.fixture))
        dataset, settlement = fx.dataset, fx.settlement
        request = request.model_copy(update={"dataset": fx.request.dataset})
    else:
        from syntha_storage.db import database_url
        from syntha_worker.loader import load_dataset

        url = (args.db or database_url()).replace("postgresql+psycopg://", "postgresql://", 1)
        loaded = load_dataset(url, request)
        dataset, settlement = loaded.dataset, loaded.settlement
    t_load = time.perf_counter()
    result = run(request, dataset, settlement)
    t_run = time.perf_counter()

    run_id = f"BT-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{request.config_hash()[:8]}"
    out = (
        Path(args.out)
        if args.out
        else Path(os.environ.get("SYNTHA_DATA_DIR", "data")) / "runs" / run_id
    )
    result.manifest["run_id"] = run_id
    result.manifest["timings_seconds"] = {
        "load": round(t_load - t0, 3),
        "engine": round(t_run - t_load, 3),
    }
    write_outputs(out, request, result)
    print(_summary(request, result, t_run - t0, out))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        dsl = StrategyDSL.model_validate_json(Path(args.strategy).read_text())
        compiled = compile_strategy(dsl, args.bar_interval)
    except (ValidationError, FeatureError) as exc:
        print(json.dumps({"ok": False, "errors": str(exc)}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "name": dsl.name,
                "dsl_hash": dsl.dsl_hash(),
                "features": [r.key for r in compiled.feature_refs],
                "lookback_bars": compiled.lookback_bars,
            },
            indent=2,
        )
    )
    return 0


def cmd_builtins(args: argparse.Namespace) -> int:
    if args.name:
        print(
            json.dumps(
                builtin_strategy(args.name).model_dump(mode="json", exclude_none=True), indent=2
            )
        )
    else:
        for key in BUILTIN_STRATEGY_FILES:
            print(f"{key:34s} {builtin_strategy(key).name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    p = argparse.ArgumentParser(
        prog="strategy-lab",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run a backtest")
    r.add_argument("config")
    r.add_argument(
        "--fixture", help="run against a golden fixture directory instead of the database"
    )
    r.add_argument(
        "--preset",
        choices=["OPTIMISTIC", "REALISTIC", "CONSERVATIVE"],
        help="override execution and fee settings with a PRD §11.6 preset",
    )
    r.add_argument("--out", help="output directory (default data/runs/<run id>)")
    r.add_argument("--db", help="database URL (default SYNTHA_DATABASE_URL)")
    r.set_defaults(func=cmd_run)
    v = sub.add_parser("validate", help="validate and compile a strategy DSL document")
    v.add_argument("strategy")
    v.add_argument("--bar-interval", type=int, default=1)
    v.set_defaults(func=cmd_validate)
    b = sub.add_parser("builtins", help="list built-in strategies, or print one")
    b.add_argument("name", nargs="?", choices=sorted(BUILTIN_STRATEGY_FILES))
    b.set_defaults(func=cmd_builtins)
    args = p.parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
