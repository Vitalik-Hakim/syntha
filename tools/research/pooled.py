"""Pool a sweep's results across collections and judge them honestly (docs/RESEARCH_PROTOCOL.md).

Per configuration:
- pooled ROI = Σ net P&L / (months × capital), with a bootstrap 90% CI over all its trades;
- months positive; median permutation percentile; pooled signals/fills; costs.
Across configurations (PRD §15.4): with N configurations tried, what does the best pooled ROI
look like by chance? Every configuration's permutation variants (its own null) give one pooled
null draw; the max over configurations, repeated, is the distribution of "best of N by chance".

Usage:
  uv run python tools/research/pooled.py --sweep discovery [--select] [--markdown out.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from register import family_counts, family_of_key

RESEARCH_DIR = Path(os.environ.get("SYNTHA_DATA_DIR", "data")) / "research"

# Pre-registered selection rule (docs/RESEARCH_PROTOCOL.md): a discovery configuration goes to
# the holdout only if all of these hold.
MIN_POOLED_TRADES = 30
MIN_MONTHS_POSITIVE_SHARE = 2 / 3
MIN_MEDIAN_PERMUTATION_PCT = 80.0
MAX_FINALISTS = 5


def load(sweep: str) -> tuple[dict[str, Any], dict[str, dict[str, dict[str, Any]]]]:
    out_dir = RESEARCH_DIR / sweep
    meta = json.loads((out_dir / "grid.json").read_text())
    rows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)  # key → collection → row
    for path in sorted(out_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rows[r["key"]][r["collection"]] = r
    return meta, rows


def pooled(
    rows: dict[str, dict[str, Any]], capital: float, rng: np.random.Generator
) -> dict[str, Any]:
    months = sorted(rows)
    good = [rows[m] for m in months if "roi" in rows[m]]
    if not good:
        return {"months": months, "error": next(iter(rows.values())).get("error")}
    n_months = len(good)
    pnl = np.array([p for r in good for p in r["trade_pnl"]])
    net = float(sum(r["net_profit"] for r in good))
    pooled_roi = net / (n_months * capital)
    if pnl.size:
        idx = rng.integers(0, pnl.size, size=(5000, pnl.size))
        boot = pnl[idx].sum(axis=1) / (n_months * capital)
        ci = [float(np.percentile(boot, 5)), float(np.percentile(boot, 95))]
    else:
        ci = None
    perms = [r["permutation_percentile"] for r in good if r["permutation_percentile"] is not None]
    return {
        "months": months,
        "n_months": n_months,
        "pooled_roi": pooled_roi,
        "pooled_ci90": ci,
        "monthly_roi": {r["collection"]: r["roi"] for r in good},
        "months_positive": sum(1 for r in good if r["roi"] > 0),
        "n_trades": int(pnl.size),
        "win_rate": float(np.mean(pnl > 0)) if pnl.size else None,
        "median_permutation_pct": float(np.median(perms)) if perms else None,
        "min_permutation_pct": float(min(perms)) if perms else None,
        "median_random_pct": float(
            np.median([r["random_percentile"] for r in good if r["random_percentile"] is not None])
        )
        if any(r["random_percentile"] is not None for r in good)
        else None,
        "buy_and_hold_pooled_roi": float(
            np.mean([r["buy_and_hold_roi"] for r in good if r["buy_and_hold_roi"] is not None])
        )
        if any(r["buy_and_hold_roi"] is not None for r in good)
        else None,
        "signals": sum(r["signals"] for r in good),
        "filled": sum(r["filled"] for r in good),
        "cost_ratio": float(
            np.median([r["cost_ratio"] for r in good if r["cost_ratio"] is not None])
        )
        if any(r["cost_ratio"] is not None for r in good)
        else None,
        "max_drawdown_pct": max(r["max_drawdown_pct"] for r in good),
        "errors": [m for m in months if "roi" not in rows[m]],
    }


def best_of_n(
    rows_by_key: dict[str, dict[str, dict[str, Any]]], draws: int, rng: np.random.Generator
) -> dict[str, Any]:
    """Distribution of the best pooled ROI among all configurations under each one's own null."""
    per_config: list[list[np.ndarray]] = []
    for key in sorted(rows_by_key):
        variants = [
            np.array(r["permutation_variants"])
            for r in rows_by_key[key].values()
            if "roi" in r and r.get("permutation_variants")
        ]
        if variants:
            per_config.append(variants)
    if not per_config:
        return {"n": 0}
    best = np.empty(draws)
    for i in range(draws):
        pooled_nulls = [
            float(np.mean([v[rng.integers(0, v.size)] for v in variants]))
            for variants in per_config
        ]
        best[i] = max(pooled_nulls)
    return {
        "n": len(per_config),
        "expected_best_roi": float(best.mean()),
        "best_p50": float(np.percentile(best, 50)),
        "best_p95": float(np.percentile(best, 95)),
        "distribution": best,
    }


def best_of_n_by_family(
    rows_by_key: dict[str, dict[str, dict[str, Any]]], draws: int, rng: np.random.Generator
) -> dict[str, dict[str, Any]]:
    """PRD v3 §3.6/§6.3: N per family comes from the register. Registered configurations that
    have no sweep result are treated as exchangeable with the family's run ones (their null draw
    is sampled from the family's pooled null draws), so an unrun registration still counts."""
    registered = family_counts()
    families: dict[str, list[list[np.ndarray]]] = defaultdict(list)
    for key in sorted(rows_by_key):
        variants = [
            np.array(r["permutation_variants"])
            for r in rows_by_key[key].values()
            if "roi" in r and r.get("permutation_variants")
        ]
        if variants:
            families[family_of_key(key)].append(variants)
    out: dict[str, dict[str, Any]] = {}
    for family, configs in families.items():
        n = max(registered.get(family, 0), len(configs))
        best = np.empty(draws)
        for i in range(draws):
            pooled_nulls = [
                float(np.mean([v[rng.integers(0, v.size)] for v in variants]))
                for variants in configs
            ]
            extra = rng.choice(pooled_nulls, size=n - len(configs)) if n > len(configs) else []
            best[i] = max([*pooled_nulls, *extra])
        out[family] = {
            "n_registered": registered.get(family, 0),
            "n_run": len(configs),
            "n": n,
            "expected_best_roi": float(best.mean()),
            "best_p95": float(np.percentile(best, 95)),
            "distribution": best,
        }
    return out


def selected(p: dict[str, Any]) -> bool:
    if "error" in p or p["pooled_ci90"] is None:
        return False
    return (
        p["pooled_ci90"][0] > 0
        and p["n_trades"] >= MIN_POOLED_TRADES
        and p["months_positive"] / p["n_months"] >= MIN_MONTHS_POSITIVE_SHARE
        and (p["median_permutation_pct"] or 0) >= MIN_MEDIAN_PERMUTATION_PCT
    )


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2%}"


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x:.0f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--select", action="store_true", help="apply the pre-registered rule")
    ap.add_argument("--markdown", help="write the table to this file too")
    ap.add_argument("--draws", type=int, default=4000)
    args = ap.parse_args()
    meta, rows = load(args.sweep)
    capital = float(meta["base"]["starting_capital"])
    rng = np.random.default_rng(20260914)
    table = {key: pooled(rows[key], capital, rng) for key in rows}
    names = {c["key"]: c["dsl"]["name"] for c in meta["configs"]}
    ranked = sorted(
        (k for k in table if "error" not in table[k]), key=lambda k: -table[k]["pooled_roi"]
    )
    null = best_of_n(rows, args.draws, rng)

    lines = []
    lines.append(
        f"## Sweep `{args.sweep}` — {len(table)} configurations, {meta['base'].get('_preset')} preset, ${capital:,.0f}"
    )
    lines.append("")
    months = sorted({m for k in rows for m in rows[k]})
    lines.append(
        f"Collections: {', '.join(months)}. Pooled ROI = Σ net P&L / (months × capital); CI is a bootstrap over all pooled trades."
    )
    lines.append("")
    if null.get("n"):
        best_key = ranked[0] if ranked else None
        best_roi = table[best_key]["pooled_roi"] if best_key else float("nan")
        share = float(np.mean(null["distribution"] >= best_roi)) if best_key else float("nan")
        lines.append(
            f"**Multiple comparisons (PRD §15.4):** {null['n']} configurations were tried. By chance alone, the best of "
            f"{null['n']} would be expected to show about {null['expected_best_roi']:+.2%} pooled ROI "
            f"(95th percentile {null['best_p95']:+.2%}). The actual best, {best_roi:+.2%}, is beaten by chance "
            f"{share:.0%} of the time."
        )
        lines.append("")
    by_family = best_of_n_by_family(rows, args.draws, rng)
    if by_family:
        lines.append("**Best-of-N by family (N from docs/research/REGISTER.md):**")
        lines.append("")
        lines.append(
            "| Family | N registered | N run | Best actual | Expected best by chance | 95th pct | Beaten by chance |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for family, null_f in sorted(by_family.items()):
            members = [k for k in ranked if family_of_key(k) == family]
            best_key = members[0] if members else None
            actual = table[best_key]["pooled_roi"] if best_key else float("nan")
            share_f = float(np.mean(null_f["distribution"] >= actual)) if best_key else float("nan")
            lines.append(
                f"| {family} | {null_f['n_registered']} | {null_f['n_run']} | {_pct(actual)} | "
                f"{_pct(null_f['expected_best_roi'])} | {_pct(null_f['best_p95'])} | {share_f:.0%} |"
            )
        lines.append("")
    header = (
        "| # | Configuration | Pooled ROI | 90% CI | Months + | Trades | Fill | Perm (median) | Random | B&H | Cost ratio | Max DD | "
        + " | ".join(m.split("-")[0] for m in months)
        + " |"
    )
    lines.append(header)
    lines.append("|" + "---|" * (header.count("|") - 1))
    for i, k in enumerate(ranked, start=1):
        p = table[k]
        ci = (
            "—"
            if p["pooled_ci90"] is None
            else f"{_pct(p['pooled_ci90'][0])} to {_pct(p['pooled_ci90'][1])}"
        )
        mark = " ✅" if args.select and selected(p) else ""
        monthly = " | ".join(_pct(p["monthly_roi"].get(m)) for m in months)
        fill = f"{p['filled']}/{p['signals']}"
        lines.append(
            f"| {i} | {names.get(k, k)}{mark} | {_pct(p['pooled_roi'])} | {ci} | {p['months_positive']}/{p['n_months']} | "
            f"{p['n_trades']} | {fill} | {_pp(p['median_permutation_pct'])} | {_pp(p['median_random_pct'])} | "
            f"{_pct(p['buy_and_hold_pooled_roi'])} | {'—' if p['cost_ratio'] is None else f'{p["cost_ratio"]:.2f}'} | "
            f"{p['max_drawdown_pct']:.1%} | {monthly} |"
        )
    errors = [k for k in table if "error" in table[k]]
    if errors:
        lines.append("")
        lines.append(f"Failed configurations: {', '.join(errors)}")
    if args.select:
        finalists = [k for k in ranked if selected(table[k])][:MAX_FINALISTS]
        lines.append("")
        lines.append(
            f"**Selection rule** (pre-registered): pooled 90% CI above zero, ≥ {MIN_POOLED_TRADES} pooled trades, "
            f"positive in ≥ {MIN_MONTHS_POSITIVE_SHARE:.0%} of months, median permutation percentile ≥ {MIN_MEDIAN_PERMUTATION_PCT:.0f}; "
            f"top {MAX_FINALISTS} by pooled ROI."
        )
        lines.append(f"**Finalists:** {', '.join(finalists) if finalists else 'none'}")
        (RESEARCH_DIR / args.sweep / "finalists.json").write_text(json.dumps(finalists, indent=1))
    text = "\n".join(lines)
    print(text)
    if args.markdown:
        Path(args.markdown).write_text(text + "\n")


if __name__ == "__main__":
    main()
