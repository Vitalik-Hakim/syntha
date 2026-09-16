"""S1 — executable calibration surface, and S2 test 1 — deadline decay (PRD v3 §5.1, §5.2).

Reads `census_observations` for the given months, computes per primary cell
(category × price bucket on the *fill* price × offset) the executable return
r = (payout − fill_10m − fee_entry_current) / fill_10m under CURRENT fees, cluster-bootstrapped
by event and by UTC day (the wider interval is the headline), plus mid calibration, fill rate,
capacity and MDE@80%, applies the pre-registered selection rule with BH at 10% FDR across the
register's cells, and writes the report. Nothing here changes the registration.

Usage:
  uv run python tools/research/s1_calibration.py discovery --months 2025-07 2025-08 ... \
      [--out research/studies/s1_calibration/report] [--resamples 10000]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import psycopg
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from register import family_counts

ROOT = Path(__file__).resolve().parents[2]
BUCKETS: list[tuple[str, float, float]] = [
    ("[0.01,0.03)", 0.01, 0.03),
    ("[0.03,0.10)", 0.03, 0.10),
    ("[0.10,0.20)", 0.10, 0.20),
    ("[0.20,0.35)", 0.20, 0.35),
    ("[0.35,0.50)", 0.35, 0.50),
    ("[0.50,0.65)", 0.50, 0.65),
    ("[0.65,0.80)", 0.65, 0.80),
    ("[0.80,0.90)", 0.80, 0.90),
    ("[0.90,0.97)", 0.90, 0.97),
    ("[0.97,0.99]", 0.97, 0.990001),
]
OFFSETS = ["-7d", "-3d", "-24h", "-6h", "-1h", "-15m"]
CATEGORIES = [
    "sports",
    "crypto",
    "politics",
    "geopolitics",
    "finance",
    "economics",
    "culture",
    "weather",
    "tech",
    "mentions",
    "other",
]
MDE_MULTIPLIER = 2.48
MONTHS_2025H2 = ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
MONTHS_2026 = ["2026-02", "2026-03", "2026-04", "2026-05"]
MIN_N = 500
MIN_N_POWER = 300
MIN_FILL_RATE = 0.30
FDR = 0.10
MAX_CANDIDATES = 8
PARTICIPATION = 0.10
SIZING_PCT = 0.02


def _url() -> str:
    url = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    return url.replace("postgresql+psycopg://", "postgresql://")


def load(months: list[str]) -> pl.DataFrame:
    ids = [f"census-{m}" for m in months]
    with psycopg.connect(_url()) as conn:
        rows = conn.execute(
            """
            SELECT o.census_id, o.condition_id, o.clob_token_id, coalesce(o.event_id, o.condition_id),
                   o.outcome_index, o.offset_label, o.ts, o.mid::float8, o.fill_10m::float8,
                   o.fill_size_10m::float8, o.liq_24h::float8, o.fee_entry_current::float8,
                   o.payout::float8, m.category, m.duration_class, m.structure, m.deadline_type,
                   o.fill_first_10m::float8
            FROM census_observations o JOIN census_markets m USING (census_id, condition_id)
            WHERE o.census_id = ANY(%s)
            """,
            (ids,),
        ).fetchall()
    df = pl.DataFrame(
        rows,
        schema=[
            "census_id",
            "condition_id",
            "clob_token_id",
            "event_id",
            "outcome_index",
            "offset",
            "ts",
            "mid",
            "fill",
            "fill_size",
            "liq_24h",
            "fee_current",
            "payout",
            "category",
            "duration_class",
            "structure",
            "deadline_type",
            "fill_first",
        ],
        orient="row",
    )
    return df.with_columns(
        pl.col("census_id").str.replace("census-", "").alias("month"),
        pl.col("ts").dt.strftime("%Y-%m-%d").alias("day"),
        ((pl.col("payout") - pl.col("fill") - pl.col("fee_current")) / pl.col("fill")).alias("r"),
        # robustness: the same return at the first qualifying print instead of the window VWAP
        (
            (pl.col("payout") - pl.col("fill_first") - pl.col("fee_current")) / pl.col("fill_first")
        ).alias("r_first"),
        (pl.col("payout") - pl.col("mid")).alias("mid_cal"),
        _bucket_expr("fill").alias("bucket"),
        _bucket_expr("mid").alias("mid_bucket"),
    )


def _bucket_expr(col: str) -> pl.Expr:
    expr: pl.Expr = pl.lit(None, dtype=pl.Utf8)
    for label, lo, hi in reversed(BUCKETS):
        expr = pl.when((pl.col(col) >= lo) & (pl.col(col) < hi)).then(pl.lit(label)).otherwise(expr)
    return expr


# ------------------------------------------------------------------ cluster bootstrap


def cluster_ci(
    values: np.ndarray, labels: np.ndarray, resamples: int, rng: np.random.Generator
) -> dict[str, Any]:
    """Mean of `values` with clusters resampled with replacement: 90% interval, SE, one-sided p."""
    if values.size == 0:
        return {"mean": None, "ci90": None, "se": None, "p_one_sided": None, "n_clusters": 0}
    _, codes = np.unique(labels, return_inverse=True)
    n_clusters = int(codes.max()) + 1
    sums = np.zeros(n_clusters)
    counts = np.zeros(n_clusters)
    np.add.at(sums, codes, values)
    np.add.at(counts, codes, 1)
    means = np.empty(resamples)
    chunk = max(1, 2_000_000 // n_clusters)
    for start in range(0, resamples, chunk):
        size = min(chunk, resamples - start)
        idx = rng.integers(0, n_clusters, size=(size, n_clusters))
        means[start : start + size] = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return {
        "mean": float(values.mean()),
        "ci90": [float(np.percentile(means, 5)), float(np.percentile(means, 95))],
        "se": float(means.std(ddof=1)) if resamples > 1 else 0.0,
        "p_one_sided": float(np.mean(means <= 0.0)),
        "n_clusters": n_clusters,
    }


def headline(
    values: np.ndarray, events: np.ndarray, days: np.ndarray, resamples: int, seed: int
) -> dict[str, Any]:
    by_event = cluster_ci(values, events, resamples, np.random.default_rng([seed, 1]))
    by_day = cluster_ci(values, days, resamples, np.random.default_rng([seed, 2]))
    if by_event["ci90"] is None:
        return {"event": by_event, "day": by_day, "key": None, **by_event}
    wider = (
        "day"
        if (by_day["ci90"][1] - by_day["ci90"][0]) > (by_event["ci90"][1] - by_event["ci90"][0])
        else "event"
    )
    head = by_day if wider == "day" else by_event
    return {"event": by_event, "day": by_day, "key": wider, **head}


# ------------------------------------------------------------------ cells


def benjamini_hochberg(pvals: dict[str, float], n_total: int, fdr: float) -> set[str]:
    """Cells rejected at `fdr` with `n_total` hypotheses (untested cells count as p = 1)."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    passed: set[str] = set()
    best_k = 0
    for k, (_, p) in enumerate(items, start=1):
        if p <= fdr * k / n_total:
            best_k = k
    for key, _ in items[:best_k]:
        passed.add(key)
    return passed


def cell_table(df: pl.DataFrame, months: list[str], resamples: int) -> pl.DataFrame:
    primary = df.filter(pl.col("duration_class") != "ultra_short")
    rows: list[dict[str, Any]] = []
    seed = 20260915
    for ci, category in enumerate(CATEGORIES):
        for bi, (bucket, _, _) in enumerate(BUCKETS):
            for oi, offset in enumerate(OFFSETS):
                cell_all = primary.filter(
                    (pl.col("category") == category) & (pl.col("offset") == offset)
                )
                with_mid = cell_all.filter(pl.col("mid_bucket") == bucket)
                filled = cell_all.filter(pl.col("bucket") == bucket).filter(
                    pl.col("r").is_not_null()
                )
                n_fill = filled.height
                n_mid = with_mid.height
                fill_rate = (
                    (with_mid.filter(pl.col("fill").is_not_null()).height / n_mid)
                    if n_mid
                    else None
                )
                row: dict[str, Any] = {
                    "category": category,
                    "bucket": bucket,
                    "offset": offset,
                    "n_fill": n_fill,
                    "n_mid": n_mid,
                    "fill_rate": fill_rate,
                    "mid_cal": float(with_mid["mid_cal"].mean())
                    if n_mid and with_mid["mid_cal"].null_count() < n_mid
                    else None,
                }
                if n_fill:
                    h = headline(
                        filled["r"].to_numpy(),
                        filled["event_id"].to_numpy(),
                        filled["day"].to_numpy(),
                        resamples,
                        seed + ci * 10_000 + bi * 100 + oi,
                    )
                    row.update(
                        {
                            "e_r": h["mean"],
                            "ci_lo": h["ci90"][0],
                            "ci_hi": h["ci90"][1],
                            "se": h["se"],
                            "cluster_key": h["key"],
                            "p_one_sided": h["p_one_sided"],
                            "mde_80": MDE_MULTIPLIER * h["se"],
                            "n_events": h["event"]["n_clusters"],
                            "e_r_first": float(filled["r_first"].mean())
                            if filled["r_first"].null_count() < n_fill
                            else None,
                        }
                    )
                    monthly = filled.group_by("month").agg(pl.col("r").mean().alias("m"))
                    signs = {r["month"]: r["m"] > 0 for r in monthly.iter_rows(named=True)}
                    row["pos_2025h2"] = sum(1 for m in MONTHS_2025H2 if signs.get(m))
                    row["seen_2025h2"] = sum(1 for m in MONTHS_2025H2 if m in signs)
                    row["pos_2026"] = sum(1 for m in MONTHS_2026 if signs.get(m))
                    row["seen_2026"] = sum(1 for m in MONTHS_2026 if m in signs)
                    n_months = max(len([m for m in months if m in set(df["month"].to_list())]), 1)
                    for capital in (1_000, 10_000):
                        requested = SIZING_PCT * capital
                        available = (
                            PARTICIPATION
                            * filled["fill_size"].to_numpy()
                            * filled["fill"].to_numpy()
                        )
                        row[f"capacity_{capital}"] = float(
                            np.minimum(requested, available).sum() / n_months
                        )
                        row[f"constrained_{capital}"] = float(np.mean(available < requested))
                else:
                    row.update(
                        {
                            "e_r": None,
                            "ci_lo": None,
                            "ci_hi": None,
                            "se": None,
                            "cluster_key": None,
                            "p_one_sided": None,
                            "mde_80": None,
                            "n_events": 0,
                            "e_r_first": None,
                            "pos_2025h2": 0,
                            "seen_2025h2": 0,
                            "pos_2026": 0,
                            "seen_2026": 0,
                            "capacity_1000": 0.0,
                            "constrained_1000": None,
                            "capacity_10000": 0.0,
                            "constrained_10000": None,
                        }
                    )
                rows.append(row)
    return pl.DataFrame(rows)


def select_candidates(
    cells: pl.DataFrame, n_register: int, months: list[str]
) -> tuple[list[dict[str, Any]], set[str]]:
    """The pre-registered rule (registration.yaml selection_rule), in order."""
    pvals = {
        f"{r['category']}|{r['bucket']}|{r['offset']}": r["p_one_sided"]
        for r in cells.iter_rows(named=True)
        if r["p_one_sided"] is not None and r["n_fill"] >= MIN_N
    }
    bh_pass = benjamini_hochberg(pvals, n_register, FDR)
    have_2025 = any(m in MONTHS_2025H2 for m in months)
    have_2026 = any(m in MONTHS_2026 for m in months)
    candidates = []
    for r in cells.iter_rows(named=True):
        key = f"{r['category']}|{r['bucket']}|{r['offset']}"
        if r["e_r"] is None or r["n_fill"] < MIN_N:
            continue
        checks = {
            "e_r_positive": r["e_r"] > 0,
            "ci_above_zero": r["ci_lo"] > 0,
            "n_ge_500": r["n_fill"] >= MIN_N,
            "fill_rate_ge_30": (r["fill_rate"] or 0) >= MIN_FILL_RATE,
            "sign_2025h2": (r["pos_2025h2"] >= 4) if have_2025 else None,
            "sign_2026": (r["pos_2026"] >= 3) if have_2026 else None,
            "bh_fdr_10": key in bh_pass,
        }
        if all(v is not False for v in checks.values()):
            candidates.append({**r, "checks": checks, "score": r["e_r"] * r["capacity_1000"]})
    candidates.sort(key=lambda c: -c["score"])
    return candidates[:MAX_CANDIDATES], bh_pass


# ------------------------------------------------------------------ S2 test 1


def s2_contrasts(df: pl.DataFrame, resamples: int) -> pl.DataFrame:
    """NO tokens on BY vs ON markets: E[r|BY] − E[r|ON] per (bucket, offset)."""
    no = df.filter(
        (pl.col("outcome_index") == 1)
        & (pl.col("duration_class") != "ultra_short")
        & pl.col("r").is_not_null()
    )
    rows = []
    seed = 20260916
    for bi, (bucket, _, _) in enumerate(BUCKETS):
        for oi, offset in enumerate(OFFSETS):
            cell = no.filter((pl.col("bucket") == bucket) & (pl.col("offset") == offset))
            by = cell.filter(pl.col("deadline_type") == "BY")
            on = cell.filter(pl.col("deadline_type") == "ON")
            row: dict[str, Any] = {
                "bucket": bucket,
                "offset": offset,
                "n_by": by.height,
                "n_on": on.height,
            }
            if by.height and on.height:
                rng = np.random.default_rng([seed, bi, oi])
                diffs = []
                for key in ("event_id", "day"):
                    a = cluster_ci(by["r"].to_numpy(), by[key].to_numpy(), resamples, rng)
                    b = cluster_ci(on["r"].to_numpy(), on[key].to_numpy(), resamples, rng)
                    # difference of independent cluster means: combine by resampling both
                    diffs.append((a, b))
                # widest interval of the difference from the two clusterings (normal approx)
                best = None
                for a, b in diffs:
                    se = float(np.hypot(a["se"], b["se"]))
                    d = a["mean"] - b["mean"]
                    ci = [d - 1.645 * se, d + 1.645 * se]
                    if best is None or (ci[1] - ci[0]) > (best["ci"][1] - best["ci"][0]):
                        best = {"d": d, "ci": ci, "se": se}
                assert best is not None
                row.update(
                    {
                        "e_r_by": float(by["r"].mean()),
                        "e_r_on": float(on["r"].mean()),
                        "diff": best["d"],
                        "ci_lo": best["ci"][0],
                        "ci_hi": best["ci"][1],
                        "mde_80": MDE_MULTIPLIER * best["se"],
                    }
                )
            else:
                row.update(
                    {
                        "e_r_by": None,
                        "e_r_on": None,
                        "diff": None,
                        "ci_lo": None,
                        "ci_hi": None,
                        "mde_80": None,
                    }
                )
            rows.append(row)
    return pl.DataFrame(rows)


# ------------------------------------------------------------------ report


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1%}"


def heatmaps(cells: pl.DataFrame, out: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = []
    for offset in OFFSETS:
        sub = cells.filter(pl.col("offset") == offset)
        fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
        for ax, (col, title, fmt) in zip(
            axes,
            [
                ("e_r", "E[r] (CURRENT fees)", "{:+.2f}"),
                ("fill_rate", "fill rate", "{:.0%}"),
                ("n_fill", "n (filled)", "{:.0f}"),
            ],
            strict=True,
        ):
            grid = np.full((len(CATEGORIES), len(BUCKETS)), np.nan)
            for r in sub.iter_rows(named=True):
                v = r[col]
                if v is not None:
                    grid[
                        CATEGORIES.index(r["category"]), [b[0] for b in BUCKETS].index(r["bucket"])
                    ] = v
            cmap = "RdYlGn" if col == "e_r" else "Blues"
            vmax = np.nanmax(np.abs(grid)) if col == "e_r" and np.isfinite(grid).any() else None
            im = ax.imshow(grid, cmap=cmap, aspect="auto", vmin=-vmax if vmax else None, vmax=vmax)
            ax.set_xticks(
                range(len(BUCKETS)), [b[0] for b in BUCKETS], rotation=60, ha="right", fontsize=8
            )
            ax.set_yticks(range(len(CATEGORIES)), CATEGORIES, fontsize=8)
            ax.set_title(f"{title} at {offset}")
            for i in range(len(CATEGORIES)):
                for j in range(len(BUCKETS)):
                    if np.isfinite(grid[i, j]):
                        ax.text(j, i, fmt.format(grid[i, j]), ha="center", va="center", fontsize=6)
            fig.colorbar(im, ax=ax, fraction=0.03)
        fig.tight_layout()
        name = f"heatmap_{offset.replace('-', 'm').replace('+', 'p')}.png"
        fig.savefig(out / name, dpi=110)
        plt.close(fig)
        files.append(name)
    return files


def write_report(
    out: Path,
    months: list[str],
    df: pl.DataFrame,
    cells: pl.DataFrame,
    candidates: list[dict[str, Any]],
    bh_pass: set[str],
    n_register: int,
    s2: pl.DataFrame,
    resamples: int,
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    cells.write_parquet(out / "calibration.parquet")
    s2.write_parquet(out / "s2_contrasts.parquet")
    ultra = df.filter(pl.col("duration_class") == "ultra_short")
    if ultra.height:
        (
            ultra.filter(pl.col("r").is_not_null())
            .group_by(["category", "bucket", "offset"])
            .agg(
                pl.len().alias("n_fill"),
                pl.col("r").mean().alias("e_r"),
                pl.col("r_first").mean().alias("e_r_first"),
                pl.col("mid_cal").mean().alias("mid_cal"),
            )
            .sort(["category", "offset", "bucket"])
            .write_parquet(out / "calibration_ultra_short.parquet")
        )
    files = heatmaps(cells, out)
    lines = [
        "# S1 — executable calibration surface: discovery report",
        "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')} by `tools/research/s1_calibration.py`.",
        "",
        "**Disclaimer first.** Every number below is in-sample on the discovery set D. A cell that",
        "passes the selection rule is a *candidate*, not a finding: it must clear C1 and C2 through",
        "the engine (PRD v3 §6.2) before any other word applies. Cells below 300 filled",
        "observations are underpowered and carry their MDE; nothing is selected from them.",
        "",
        f"Months: {', '.join(months)}. Observations: {df.height:,} ({df.filter(pl.col('r').is_not_null()).height:,} with an executable fill).",
        f"Register N (primary cells, `s1_calibration:cells`): {n_register}. Cluster bootstrap: {resamples:,} resamples by event and by UTC day; the wider interval is shown.",
        "",
        "## Verdict",
        "",
    ]
    if candidates:
        lines.append(
            f"**{len(candidates)} candidate cell(s)** cleared the pre-registered rule (BH 10% FDR across {n_register} cells; {len(bh_pass)} cells pass BH before the other conditions)."
        )
        lines.append("")
        lines.append(
            "| # | Category | Bucket | Offset | n | fill rate | E[r] | 90% CI (key) | E[r] first print | mid cal. | MDE@80% | months + (25H2 / 26) | capacity $1k/mo | $10k/mo |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(candidates, start=1):
            lines.append(
                f"| {i} | {c['category']} | {c['bucket']} | {c['offset']} | {c['n_fill']:,} | {c['fill_rate']:.0%} | {c['e_r']:+.3f} | "
                f"{c['ci_lo']:+.3f} to {c['ci_hi']:+.3f} ({c['cluster_key']}) | {_pct(c['e_r_first'])} | {_pct(c['mid_cal'])} | {c['mde_80']:.3f} | {c['pos_2025h2']}/{c['seen_2025h2']} / {c['pos_2026']}/{c['seen_2026']} | "
                f"${c['capacity_1000']:,.0f} | ${c['capacity_10000']:,.0f} |"
            )
    else:
        lines.append(
            f"**Candidates: none.** No primary cell cleared the pre-registered rule (BH 10% FDR across {n_register} register cells; {len(bh_pass)} cells passed BH alone)."
        )
    lines.append("")
    excluded = df.filter(pl.col("duration_class") == "ultra_short")
    lines.append(
        f"Excluded from primary cells as `ultra_short` (question window ≤ 60 min or end − open ≤ 60 min): "
        f"{excluded.select('condition_id').n_unique():,} markets, {excluded.height:,} observations "
        f"({excluded.filter(pl.col('r').is_not_null()).height:,} with a fill). They are measured in "
        "`calibration_ultra_short.parquet`, never pooled."
    )
    lines.append("")
    lines.append("## Coverage per offset")
    lines.append("")
    lines.append("| Offset | observations | with mid | with fill | fill rate |")
    lines.append("|---|---|---|---|---|")
    for offset in OFFSETS:
        sub = df.filter(pl.col("offset") == offset)
        n = sub.height
        n_mid = sub.filter(pl.col("mid").is_not_null()).height
        n_fill = sub.filter(pl.col("fill").is_not_null()).height
        lines.append(
            f"| {offset} | {n:,} | {n_mid:,} | {n_fill:,} | {n_fill / n if n else 0:.1%} |"
        )
    lines.append("")
    lines.append("## Heatmaps (E[r], fill rate, n per offset)")
    lines.append("")
    for f in files:
        lines.append(f"![{f}]({f})")
    lines.append("")
    lines.append(
        "## Negative space — every primary cell with data (n ≥ 30): what could have been missed"
    )
    lines.append("")
    lines.append(
        "| Category | Bucket | Offset | n | fill rate | E[r] | 90% CI | E[r] first print | MDE@80% | mid cal. | months + | status |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    tested = cells.filter(pl.col("n_fill") >= 30).sort(["offset", "category", "bucket"])
    for r in tested.iter_rows(named=True):
        key = f"{r['category']}|{r['bucket']}|{r['offset']}"
        status = (
            "candidate"
            if any(
                c["category"] == r["category"]
                and c["bucket"] == r["bucket"]
                and c["offset"] == r["offset"]
                for c in candidates
            )
            else "underpowered"
            if r["n_fill"] < MIN_N_POWER
            else "thin fills"
            if (r["fill_rate"] or 0) < MIN_FILL_RATE
            else "BH pass, other rule failed"
            if key in bh_pass
            else "no effect detected"
        )
        lines.append(
            f"| {r['category']} | {r['bucket']} | {r['offset']} | {r['n_fill']:,} | {r['fill_rate']:.0%} | {r['e_r']:+.3f} | "
            f"{r['ci_lo']:+.3f} to {r['ci_hi']:+.3f} | {_pct(r['e_r_first'])} | {r['mde_80']:.3f} | {_pct(r['mid_cal'])} | {r['pos_2025h2']}/{r['seen_2025h2']} / {r['pos_2026']}/{r['seen_2026']} | {status} |"
        )
    n_empty = cells.filter(pl.col("n_fill") < 30).height
    lines.append("")
    lines.append(
        f"{n_empty} of {cells.height} primary cells have fewer than 30 filled observations and are not listed (no test possible)."
    )
    lines.append("")
    lines.append("## S2 test 1 — deadline decay (NO tokens, BY vs ON, per bucket × offset)")
    lines.append("")
    lines.append(
        "| Bucket | Offset | n BY | n ON | E[r] BY | E[r] ON | difference | 90% CI | MDE@80% |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in s2.filter(pl.col("diff").is_not_null()).iter_rows(named=True):
        lines.append(
            f"| {r['bucket']} | {r['offset']} | {r['n_by']:,} | {r['n_on']:,} | {r['e_r_by']:+.3f} | {r['e_r_on']:+.3f} | {r['diff']:+.3f} | "
            f"{r['ci_lo']:+.3f} to {r['ci_hi']:+.3f} | {r['mde_80']:.3f} |"
        )
    n_s2 = s2.filter(pl.col("diff").is_not_null()).height
    lines.append("")
    lines.append(
        f"{n_s2} of 60 contrast cells have both arms; the S2 rule needs n ≥ 500 per arm and BH across the 60 register cells."
    )
    s2_pass = [
        r
        for r in s2.iter_rows(named=True)
        if r["diff"] is not None and r["n_by"] >= MIN_N and r["n_on"] >= MIN_N and r["ci_lo"] > 0
    ]
    lines.append(f"Contrasts with n ≥ 500 per arm and the wider CI above zero: {len(s2_pass)}.")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    summary = {
        "months": months,
        "observations": df.height,
        "with_fill": df.filter(pl.col("r").is_not_null()).height,
        "n_register": n_register,
        "bh_pass": sorted(bh_pass),
        "candidates": [
            {k: v for k, v in c.items() if k != "checks"} | {"checks": c["checks"]}
            for c in candidates
        ],
        "s2_contrasts_with_both_arms": n_s2,
        "s2_pass": [f"{r['bucket']}|{r['offset']}" for r in s2_pass],
        "resamples": resamples,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    return out / "report.md"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("discovery")
    p.add_argument("--months", nargs="+", required=True)
    p.add_argument(
        "--out", default=str(ROOT / "research" / "studies" / "s1_calibration" / "report")
    )
    p.add_argument("--resamples", type=int, default=10_000)
    args = ap.parse_args()

    registration = yaml.safe_load(
        (ROOT / "research/studies/s1_calibration/registration.yaml").read_text()
    )
    n_register = family_counts().get("s1_calibration:cells", 0)
    assert n_register == 660, (
        f"register has {n_register} S1 cells; expected 660 (registration: {registration['primary_cells']})"
    )
    df = load(args.months)
    print(
        f"{df.height:,} observations over {len(args.months)} month(s); computing {len(CATEGORIES) * len(BUCKETS) * len(OFFSETS)} cells",
        flush=True,
    )
    cells = cell_table(df, args.months, args.resamples)
    candidates, bh_pass = select_candidates(cells, n_register, args.months)
    s2 = s2_contrasts(df, args.resamples)
    path = write_report(
        Path(args.out), args.months, df, cells, candidates, bh_pass, n_register, s2, args.resamples
    )
    print(f"report → {path}; candidates: {len(candidates)}; BH passes: {len(bh_pass)}")


if __name__ == "__main__":
    main()
