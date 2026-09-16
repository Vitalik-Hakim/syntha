"""Confirmation runs and §6.2 verdicts (PRD v3): candidate strategies through the engine on a
set of census months, pooled honestly.

Per strategy × month, two persisted runs: the deciding run — Realistic execution with CURRENT
fees — and the Conservative preset (also CURRENT fees). Per strategy × set the script pools the
executed trades of the deciding runs across months, cluster-bootstraps return per dollar by
event and by UTC day (wider = headline), counts months with a positive pooled return, averages
capacity at $1,000, and applies the set's rule:

  E[r] under CURRENT fees > 0, the wider cluster 90% CI above zero, sign positive in >= 2/3 of
  the set's months; for a final verdict the Conservative runs pooled over C1 ∪ C2 must also be
  positive and capacity >= $50/month at $1,000 (§6.2).

Usage:
  uv run python tools/research/confirm.py run --study s2 --set D --months 2025-07 ... --out DIR FILE.json
  uv run python tools/research/confirm.py run --study s1 --set C1 --months 2026-06 2026-07 2026-08 --out DIR FILE.json ...
  uv run python tools/research/confirm.py run --study s1 --set C2 --months 2026-01 --out DIR FILE.json ...
  uv run python tools/research/confirm.py verdict --out DIR      # combine the sets' JSON files
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import psycopg

from syntha_contracts.backtest import BacktestRequest, FeeConfig
from syntha_contracts.enums import FeeMode
from syntha_engine.honesty import CLUSTER_KEYS, MDE_MULTIPLIER, cluster_bootstrap
from syntha_worker.loader import LoadedDataset, load_dataset
from syntha_worker.runs import create_run, execute_run

sys.path.insert(0, str(Path(__file__).parent))
from register import require_registered

ROOT = Path(__file__).resolve().parents[2]
MIN_CAPACITY_PER_MONTH = 50.0
SIGN_SHARE = 2 / 3
BOOT = 10_000


def _url() -> str:
    url = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    return url.replace("postgresql+psycopg://", "postgresql://")


def _trades(conn: psycopg.Connection[Any], run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.seq, t.fill_status, t.net_pnl::text, t.notional::text, t.condition_id,
               coalesce(m.event_id, t.condition_id), t.entry_ts, t.basket_role
        FROM bt_trades t LEFT JOIN markets m ON m.condition_id = t.condition_id
        WHERE t.run_id = %s ORDER BY t.seq
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "seq": seq,
            "fill_status": status,
            "net_pnl": net,
            "notional": notional,
            "condition_id": cid,
            "_event": ev,
            "entry_ts": ts.isoformat(),
            "basket_role": role,
        }
        for seq, status, net, notional, cid, ev, ts, role in rows
    ]


def pooled(trades: list[dict[str, Any]], capital: Decimal) -> dict[str, Any]:
    executed = [
        t
        for t in trades
        if t["fill_status"] in ("FILLED", "PARTIAL") and t.get("basket_role") != "LEG"
    ]
    if not executed:
        return {"n_trades": 0, "return_per_dollar": None, "ci90": None, "key": None, "mde_80": None}
    labels = {
        "event": [t["_event"] for t in executed],
        "day": [t["entry_ts"][:10] for t in executed],
    }
    out = {
        k: cluster_bootstrap(executed, labels[k], capital, BOOT, np.random.default_rng([7, i]), k)
        for i, k in enumerate(CLUSTER_KEYS)
    }
    widths = {
        k: out[k]["return_per_dollar_ci90"][1] - out[k]["return_per_dollar_ci90"][0]
        for k in CLUSTER_KEYS
    }
    key = max(CLUSTER_KEYS, key=lambda k: widths[k])
    net = sum(Decimal(t["net_pnl"]) for t in executed)
    notional = sum(Decimal(t["notional"]) for t in executed)
    return {
        "n_trades": len(executed),
        "net": float(net),
        "notional": float(notional),
        "return_per_dollar": float(net / notional) if notional else None,
        "ci90": out[key]["return_per_dollar_ci90"],
        "key": key,
        "se": out[key]["se_return_per_dollar"],
        "mde_80": MDE_MULTIPLIER * out[key]["se_return_per_dollar"],
        "by_key": {k: out[k]["return_per_dollar_ci90"] for k in CLUSTER_KEYS},
    }


def run_set(args: argparse.Namespace) -> None:
    url = _url()
    documents = [(p, json.loads(Path(p).read_text())) for p in args.files]
    require_registered(documents)
    capital = Decimal(args.capital)
    cache: dict[tuple[Any, ...], LoadedDataset] = {}
    results: dict[str, dict[str, Any]] = {}
    with psycopg.connect(url) as conn:
        for path, dsl in documents:
            per_month: dict[str, dict[str, Any]] = {}
            all_trades: list[dict[str, Any]] = []
            cons_trades: list[dict[str, Any]] = []
            for month in args.months:
                collection = f"census-{month}"
                base = BacktestRequest.model_validate(
                    {
                        "strategy": dsl,
                        "dataset": {"collection": collection},
                        "starting_capital": args.capital,
                        "seed": args.seed,
                        "bar_interval_minutes": 60,
                    }
                )
                deciding = base.with_preset("REALISTIC").model_copy(
                    update={"fees": FeeConfig(mode=FeeMode.CURRENT)}
                )
                conservative = base.with_preset("CONSERVATIVE")
                month_out: dict[str, Any] = {}
                for label, request in (("deciding", deciding), ("conservative", conservative)):
                    run_id = create_run(
                        conn, request, note=f"{args.study} {args.set} {label} {month}"
                    )
                    conn.commit()
                    u = request.strategy.universe
                    key = (
                        collection,
                        u.side,
                        tuple(u.categories or ()),
                        tuple(u.deadline_types or ()),
                    )
                    if key not in cache:
                        cache[key] = load_dataset(url, request)
                    status = execute_run(run_id, url, loaded=cache[key])
                    row = conn.execute(
                        "SELECT metrics, data_quality FROM backtest_runs WHERE id = %s", (run_id,)
                    ).fetchone()
                    m, dq = (row[0] or {}, row[1] or {}) if row else ({}, {})
                    trades = _trades(conn, run_id) if status.value == "DONE" else []
                    month_out[label] = {
                        "run_id": run_id,
                        "status": status.value,
                        "roi": m.get("roi"),
                        "n_trades": m.get("n_trades"),
                        "return_per_dollar": (m.get("return_per_dollar") or {}).get("pooled"),
                        "capacity_1000": ((m.get("capacity") or {}).get("1000") or {}).get(
                            "notional_per_month"
                        ),
                        "signals": dq.get("signals_generated"),
                        "filled": dq.get("signals_filled"),
                    }
                    (all_trades if label == "deciding" else cons_trades).extend(trades)
                    print(
                        json.dumps({"strategy": dsl["name"], "month": month, **month_out[label]}),
                        flush=True,
                    )
                per_month[month] = month_out
            pool = pooled(all_trades, capital)
            cons_pool = pooled(cons_trades, capital)
            months_positive = sum(
                1
                for month in args.months
                if (per_month[month]["deciding"]["return_per_dollar"] or 0) > 0
            )
            months_with_trades = sum(
                1 for month in args.months if (per_month[month]["deciding"]["n_trades"] or 0) > 0
            )
            capacity = [
                per_month[m]["deciding"]["capacity_1000"]
                for m in args.months
                if per_month[m]["deciding"]["capacity_1000"] is not None
            ]
            passes = (
                pool["return_per_dollar"] is not None
                and pool["return_per_dollar"] > 0
                and pool["ci90"][0] > 0
                and months_positive >= SIGN_SHARE * len(args.months)
            )
            results[dsl["name"]] = {
                "file": path,
                "set": args.set,
                "months": args.months,
                "per_month": per_month,
                "pooled_deciding": pool,
                "pooled_conservative": cons_pool,
                "months_positive": months_positive,
                "months_with_trades": months_with_trades,
                "capacity_1000_mean": float(np.mean(capacity)) if capacity else None,
                "passes_set_rule": passes,
            }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"confirmation_{args.set}.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "results": results},
            indent=1,
            default=str,
        )
        + "\n"
    )
    lines = [f"## {args.study} on {args.set} ({', '.join(args.months)})", ""]
    lines.append(
        "| Strategy | Trades | Return/$ (pooled, CURRENT fees) | 90% CI (key) | MDE@80% | months + | Conservative return/$ | capacity $1k/mo | set rule |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        p, c = r["pooled_deciding"], r["pooled_conservative"]
        rp = "—" if p["return_per_dollar"] is None else f"{p['return_per_dollar']:+.3f}"
        ci = (
            "—" if p["ci90"] is None else f"{p['ci90'][0]:+.3f} to {p['ci90'][1]:+.3f} ({p['key']})"
        )
        mde = "—" if p["mde_80"] is None else f"{p['mde_80']:.3f}"
        cp = "—" if c["return_per_dollar"] is None else f"{c['return_per_dollar']:+.3f}"
        cap = "—" if r["capacity_1000_mean"] is None else f"${r['capacity_1000_mean']:,.0f}"
        lines.append(
            f"| {name} | {p['n_trades']} | {rp} | {ci} | {mde} | {r['months_positive']}/{len(args.months)} | {cp} | {cap} | {'pass' if r['passes_set_rule'] else 'fail'} |"
        )
    (out / f"confirmation_{args.set}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def verdict(args: argparse.Namespace) -> None:
    out = Path(args.out)
    sets = {}
    for name in ("D", "C1", "C2"):
        path = out / f"confirmation_{name}.json"
        if path.exists():
            sets[name] = json.loads(path.read_text())["results"]
    names = sorted({n for s in sets.values() for n in s})
    lines = [
        "## Verdicts (PRD v3 §6.2)",
        "",
        "| Strategy | D | C1 | C2 | Conservative C1∪C2 | capacity | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    verdicts = {}
    for name in names:
        d = sets.get("D", {}).get(name)
        c1 = sets.get("C1", {}).get(name)
        c2 = sets.get("C2", {}).get(name)
        cons_pos = None
        if c1 or c2:
            nets = sum((r["pooled_conservative"].get("net") or 0.0) for r in (c1, c2) if r)
            cons_pos = nets > 0
        caps = [
            r["capacity_1000_mean"] for r in (c1, c2) if r and r["capacity_1000_mean"] is not None
        ]
        capacity_ok = bool(caps) and float(np.mean(caps)) >= MIN_CAPACITY_PER_MONTH
        c2_complete = c2 is not None and len(c2["months"]) >= 3
        if (
            d
            and d["passes_set_rule"]
            and c1
            and c1["passes_set_rule"]
            and c2
            and c2["passes_set_rule"]
            and cons_pos
            and capacity_ok
        ):
            v = (
                "confirmed"
                if c2_complete
                else "confirmed on the C2 months available (provisional until 2026-09/10 resolve)"
            )
        elif d and d["passes_set_rule"] and c1 and c1["passes_set_rule"]:
            v = "promising / unconfirmed" if c2 else "promising / unconfirmed (C2 pending)"
        else:
            v = "rejected"
        verdicts[name] = v
        cell = lambda r: "—" if r is None else ("pass" if r["passes_set_rule"] else "fail")  # noqa: E731
        lines.append(
            f"| {name} | {cell(d)} | {cell(c1)} | {cell(c2)} | {'—' if cons_pos is None else ('positive' if cons_pos else 'negative')} | {'ok' if capacity_ok else 'below $50/mo'} | **{v}** |"
        )
    (out / "verdicts.md").write_text("\n".join(lines) + "\n")
    (out / "verdicts.json").write_text(json.dumps(verdicts, indent=1) + "\n")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("files", nargs="+")
    r.add_argument("--study", required=True)
    r.add_argument("--set", required=True, choices=["D", "C1", "C2"])
    r.add_argument("--months", nargs="+", required=True)
    r.add_argument("--capital", default="1000")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--out", required=True)
    r.set_defaults(func=run_set)
    v = sub.add_parser("verdict")
    v.add_argument("--out", required=True)
    v.set_defaults(func=verdict)
    args = ap.parse_args()
    if not args.cmd:
        ap.error("choose run or verdict")
    args.func(args)


if __name__ == "__main__":
    main()
