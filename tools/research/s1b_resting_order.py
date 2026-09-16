"""S1b — resting-order return at taker-BUY prints: the maker side of the calibration census
(research/studies/s1b_resting_order/registration.yaml).

Unit: one taker-BUY print inside a census observation's 10-minute fill window. Estimand:
r_maker = (p − payout) / (1 − p) — the maker sold the token at p and holds the complement to
resolution; no taker fee; rebate 0 (headline) and 0.25 × the CURRENT taker fee (sensitivity).
Adverse selection: the position marked 5 and 30 minutes after the fill from a 1-minute bar
where one exists, else the next print. Primary family: print price < 0.35 at offsets ≤ 24h
(176 register cells); everything else is secondary and reported only.

Usage:
  uv run python tools/research/s1b_resting_order.py discovery --months 2025-07 ... 2026-05
"""

from __future__ import annotations

import argparse
import json
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
from s1_calibration import (
    BUCKETS,
    CATEGORIES,
    MDE_MULTIPLIER,
    MONTHS_2025H2,
    MONTHS_2026,
    OFFSETS,
    PARTICIPATION,
    SIZING_PCT,
    _bucket_expr,
    _url,
    benjamini_hochberg,
    headline,
)

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "research" / "studies" / "s1b_resting_order"
PRIMARY_BUCKETS = ["[0.01,0.03)", "[0.03,0.10)", "[0.10,0.20)", "[0.20,0.35)"]
PRIMARY_OFFSETS = ["-24h", "-6h", "-1h", "-15m"]
FILL_WINDOW_S = 600
MARKS_MIN = (5, 30)
MARK_FALLBACK_H = 24
REBATE = 0.25
MIN_N = 500
MIN_EVENTS = 30
MIN_N_POWER = 300
MIN_POS_MONTHS = 7
MIN_CAPACITY = 50.0
MAX_CANDIDATES = 8
FDR = 0.10
SEED = 20260916

FILLS_SQL = """
WITH obs AS (
    SELECT o.census_id, o.condition_id, o.clob_token_id,
           coalesce(o.event_id, o.condition_id) AS event_id, o.offset_label, o.ts AS t0,
           o.payout::float8 AS payout, m.category, m.duration_class
    FROM census_observations o JOIN census_markets m USING (census_id, condition_id)
    WHERE o.census_id = %(census_id)s
)
SELECT obs.census_id, obs.condition_id, obs.clob_token_id, obs.event_id, obs.offset_label,
       obs.t0, obs.payout, obs.category, obs.duration_class,
       p.id AS print_id, p.ts AS t_p, p.price::float8 AS p, p.size::float8 AS size
FROM obs JOIN trade_prints p
  ON p.clob_token_id = obs.clob_token_id
 AND p.ts >= obs.t0 AND p.ts < obs.t0 + interval '10 minutes'
 AND p.side = 'BUY'
 AND p.ts >= %(lo)s AND p.ts < %(hi)s   -- chunk exclusion: the month's window
"""

# every print (any side) and every 1-minute bar of the month's census tokens, for the marks
PRINTS_SQL = """
SELECT p.clob_token_id, p.ts, p.price::float8
FROM trade_prints p
WHERE p.clob_token_id IN (SELECT clob_token_id FROM census_observations WHERE census_id = %(census_id)s)
  AND p.ts >= %(lo)s AND p.ts < %(hi)s
"""
BARS_SQL = """
SELECT b.clob_token_id, b.ts, b.price::float8, b.staleness_minutes
FROM price_bars b
WHERE b.clob_token_id IN (SELECT clob_token_id FROM census_observations WHERE census_id = %(census_id)s)
  AND b.source = 'prices_history' AND b.ts >= %(lo)s AND b.ts < %(hi)s
"""
BOUNDS_SQL = """
SELECT min(ts), max(ts) + interval '10 minutes' + interval '30 minutes' + interval '24 hours'
FROM census_observations WHERE census_id = %(census_id)s
"""

TS = pl.Datetime("us", "UTC")
FILL_SCHEMA: dict[str, Any] = {
    "census_id": pl.Utf8,
    "condition_id": pl.Utf8,
    "clob_token_id": pl.Utf8,
    "event_id": pl.Utf8,
    "offset": pl.Utf8,
    "t0": TS,
    "payout": pl.Float64,
    "category": pl.Utf8,
    "duration_class": pl.Utf8,
    "print_id": pl.Int64,
    "t_p": TS,
    "p": pl.Float64,
    "size": pl.Float64,
}
PRINT_SCHEMA: dict[str, Any] = {"clob_token_id": pl.Utf8, "ts": TS, "price": pl.Float64}
BAR_SCHEMA: dict[str, Any] = {
    "clob_token_id": pl.Utf8,
    "ts": TS,
    "price": pl.Float64,
    "staleness_minutes": pl.Int64,
}
CACHE = ROOT / "data" / "checkpoints" / "s1b"


def _marks(fills: pl.DataFrame, prints: pl.DataFrame, bars: pl.DataFrame) -> pl.DataFrame:
    """bar5/stale5/bar30/stale30 (the 1-minute bar at the mark minute) and next5/next30 (the
    first print of any side at or after the mark time, within 24 hours) for every fill."""
    prints = prints.sort(["clob_token_id", "ts"])
    out = fills.with_row_index("_i")
    for d in MARKS_MIN:
        key = f"_m{d}"
        with_key = out.with_columns(
            (pl.col("t_p") + pl.duration(minutes=d)).alias(key),
            (pl.col("t_p").dt.truncate("1m") + pl.duration(minutes=d)).alias(f"{key}_bar"),
        )
        nxt = (
            with_key.select("_i", "clob_token_id", key)
            .sort(key)
            .join_asof(
                prints.rename({"ts": key, "price": f"next{d}"}),
                on=key,
                by="clob_token_id",
                strategy="forward",
                tolerance=f"{MARK_FALLBACK_H}h",
            )
            .select("_i", f"next{d}")
        )
        bar = (
            with_key.select("_i", "clob_token_id", f"{key}_bar")
            .join(
                bars.rename(
                    {"ts": f"{key}_bar", "price": f"bar{d}", "staleness_minutes": f"stale{d}"}
                ),
                on=["clob_token_id", f"{key}_bar"],
                how="left",
            )
            .select("_i", f"bar{d}", f"stale{d}")
        )
        out = out.join(nxt, on="_i", how="left").join(bar, on="_i", how="left")
    return out.drop("_i")


def current_fee_terms_by_category() -> dict[str, tuple[float, int]]:
    """(rate, exponent) of each category's latest schedule — the CURRENT taker fee (v3 §3.5)."""
    from syntha_ingestion.census import current_fee_terms, fee_entries

    schedules = fee_entries()
    out: dict[str, tuple[float, int]] = {}
    for category in CATEGORIES:
        terms = current_fee_terms(category, schedules)
        if terms is not None:
            out[category] = (float(terms.rate), int(terms.exponent))
    return out


def load_month(conn: psycopg.Connection[Any], month: str) -> pl.DataFrame:
    """One month's fills with their marks; cached as parquet so a rerun does not requery."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{month}.parquet"
    if cached.exists():
        df = pl.read_parquet(cached)
        print(f"  census-{month}: {df.height:,} fills (cached)", flush=True)
        return df
    params: dict[str, Any] = {"census_id": f"census-{month}"}
    lo, hi = conn.execute(BOUNDS_SQL, params).fetchone()  # type: ignore[misc]
    params |= {"lo": lo, "hi": hi}
    fills = pl.DataFrame(
        conn.execute(FILLS_SQL, params).fetchall(), schema=FILL_SCHEMA, orient="row"
    )
    prints = pl.DataFrame(
        conn.execute(PRINTS_SQL, params).fetchall(), schema=PRINT_SCHEMA, orient="row"
    )
    bars = pl.DataFrame(conn.execute(BARS_SQL, params).fetchall(), schema=BAR_SCHEMA, orient="row")
    df = _marks(fills, prints, bars).drop("print_id")
    df.write_parquet(cached)
    print(
        f"  census-{month}: {fills.height:,} taker-BUY prints in fill windows "
        f"({prints.height:,} prints and {bars.height:,} 1-minute bars for the marks)",
        flush=True,
    )
    return df


def load(months: list[str]) -> pl.DataFrame:
    frames = []
    with psycopg.connect(_url()) as conn:
        conn.execute("SET statement_timeout = 0")
        # Docker's 64 MB /dev/shm cannot hold parallel workers' hash tables on 76M prints
        conn.execute("SET max_parallel_workers_per_gather = 0")
        for month in months:
            frames.append(load_month(conn, month))
    df = pl.concat(frames)
    fees = current_fee_terms_by_category()
    fee_expr: pl.Expr = pl.lit(0.0)
    for category, (rate, exponent) in fees.items():
        fee_expr = (
            pl.when(pl.col("category") == category)
            .then(rate * (pl.col("p") * (1 - pl.col("p"))) ** exponent)
            .otherwise(fee_expr)
        )
    mark5 = (
        pl.when(pl.col("bar5").is_not_null() & (pl.col("stale5") <= 5))
        .then(pl.col("bar5"))
        .otherwise(pl.col("next5"))
    )
    mark30 = (
        pl.when(pl.col("bar30").is_not_null() & (pl.col("stale30") <= 30))
        .then(pl.col("bar30"))
        .otherwise(pl.col("next30"))
    )
    return (
        df.filter(pl.col("payout").is_not_null() & (pl.col("p") > 0.01) & (pl.col("p") < 0.99))
        .with_columns(
            pl.col("census_id").str.replace("census-", "").alias("month"),
            pl.col("t_p").dt.strftime("%Y-%m-%d").alias("day"),
            _bucket_expr("p").alias("bucket"),
            fee_expr.alias("taker_fee_per_share"),
            mark5.alias("mark5"),
            mark30.alias("mark30"),
            ((pl.col("p") - pl.col("payout")) / (1 - pl.col("p"))).alias("r"),
            ((1 - pl.col("p")) * pl.col("size")).alias("maker_dollars"),
        )
        .with_columns(
            (pl.col("r") + REBATE * pl.col("taker_fee_per_share") / (1 - pl.col("p"))).alias(
                "r_rebate"
            ),
            ((pl.col("p") - pl.col("mark5")) / (1 - pl.col("p"))).alias("as5"),
            ((pl.col("p") - pl.col("mark30")) / (1 - pl.col("p"))).alias("as30"),
            (pl.col("bar5").is_not_null() & (pl.col("stale5") <= 5)).alias("mark5_from_bar"),
        )
    )


def _mean(s: pl.Series) -> float | None:
    return float(s.mean()) if s.len() and s.null_count() < s.len() else None


def cell_table(df: pl.DataFrame, months: list[str], resamples: int) -> pl.DataFrame:
    primary = df.filter(pl.col("duration_class") != "ultra_short")
    rows: list[dict[str, Any]] = []
    n_months = max(len(months), 1)
    for ci, category in enumerate(CATEGORIES):
        for bi, (bucket, _, _) in enumerate(BUCKETS):
            for oi, offset in enumerate(OFFSETS):
                cell = primary.filter(
                    (pl.col("category") == category)
                    & (pl.col("bucket") == bucket)
                    & (pl.col("offset") == offset)
                )
                n = cell.height
                row: dict[str, Any] = {
                    "category": category,
                    "bucket": bucket,
                    "offset": offset,
                    "family": "primary"
                    if bucket in PRIMARY_BUCKETS and offset in PRIMARY_OFFSETS
                    else "secondary",
                    "n": n,
                    "n_events": int(cell["event_id"].n_unique()) if n else 0,
                    "n_markets": int(cell["condition_id"].n_unique()) if n else 0,
                    # the maker's loss events: prints whose token paid out (r = −1)
                    "n_wins": int((cell["payout"] >= 0.5).sum()) if n else 0,
                    "n_win_events": int(cell.filter(pl.col("payout") >= 0.5)["event_id"].n_unique())
                    if n
                    else 0,
                }
                if n:
                    h = headline(
                        cell["r"].to_numpy(),
                        cell["event_id"].to_numpy(),
                        cell["day"].to_numpy(),
                        resamples,
                        SEED + ci * 10_000 + bi * 100 + oi,
                    )
                    w = cell["maker_dollars"].to_numpy()
                    monthly = cell.group_by("month").agg(pl.col("r").mean().alias("m"))
                    signs = {r["month"]: r["m"] > 0 for r in monthly.iter_rows(named=True)}
                    windows = cell.group_by(["clob_token_id", "offset", "t0"]).agg(
                        pl.col("maker_dollars").sum().alias("flow")
                    )
                    capacity = float(
                        np.minimum(
                            SIZING_PCT * 1_000, PARTICIPATION * windows["flow"].to_numpy()
                        ).sum()
                        / n_months
                    )
                    row.update(
                        {
                            "r_maker": h["mean"],
                            "ci_lo": h["ci90"][0],
                            "ci_hi": h["ci90"][1],
                            "se": h["se"],
                            "cluster_key": h["key"],
                            "p_one_sided": h["p_one_sided"],
                            "mde_80": MDE_MULTIPLIER * h["se"],
                            "r_size_weighted": float(np.average(cell["r"].to_numpy(), weights=w))
                            if w.sum() > 0
                            else None,
                            "r_rebate": _mean(cell["r_rebate"]),
                            "as5": _mean(cell["as5"]),
                            "as30": _mean(cell["as30"]),
                            "as5_coverage": 1 - cell["as5"].null_count() / n,
                            "as30_coverage": 1 - cell["as30"].null_count() / n,
                            "mark5_bar_share": float(cell["mark5_from_bar"].mean()),
                            "pos_months": sum(1 for m in months if signs.get(m)),
                            "seen_months": sum(1 for m in months if m in signs),
                            "pos_2025h2": sum(1 for m in MONTHS_2025H2 if signs.get(m)),
                            "pos_2026": sum(1 for m in MONTHS_2026 if signs.get(m)),
                            "capacity_1000": capacity,
                            "mean_p": float(cell["p"].mean()),
                            "flow_per_month": float(cell["maker_dollars"].sum() / n_months),
                        }
                    )
                else:
                    row.update(
                        {
                            k: None
                            for k in (
                                "r_maker",
                                "ci_lo",
                                "ci_hi",
                                "se",
                                "cluster_key",
                                "p_one_sided",
                                "mde_80",
                                "r_size_weighted",
                                "r_rebate",
                                "as5",
                                "as30",
                                "as5_coverage",
                                "as30_coverage",
                                "mark5_bar_share",
                                "mean_p",
                            )
                        }
                    )
                    row.update(
                        {
                            "pos_months": 0,
                            "seen_months": 0,
                            "pos_2025h2": 0,
                            "pos_2026": 0,
                            "capacity_1000": 0.0,
                            "flow_per_month": 0.0,
                        }
                    )
                rows.append(row)
    return pl.DataFrame(rows)


def select_candidates(
    cells: pl.DataFrame, n_register: int
) -> tuple[list[dict[str, Any]], set[str]]:
    """The registered rule, in order; BH is descriptive only (registration.yaml)."""
    prim = cells.filter(pl.col("family") == "primary")
    pvals = {
        f"{r['category']}|{r['bucket']}|{r['offset']}": r["p_one_sided"]
        for r in prim.iter_rows(named=True)
        if r["p_one_sided"] is not None and r["n"] >= MIN_N
    }
    bh_pass = benjamini_hochberg(pvals, n_register, FDR)
    candidates = []
    for r in prim.iter_rows(named=True):
        if r["r_maker"] is None or r["n"] < MIN_N:
            continue
        checks = {
            "ci_above_zero": r["ci_lo"] > 0,
            "pos_months_ge_7": r["pos_months"] >= MIN_POS_MONTHS,
            "capacity_ge_50": r["capacity_1000"] >= MIN_CAPACITY,
            "n_ge_500_events_ge_30": r["n"] >= MIN_N and r["n_events"] >= MIN_EVENTS,
        }
        if all(checks.values()):
            candidates.append({**r, "checks": checks, "score": r["r_maker"] * r["capacity_1000"]})
    candidates.sort(key=lambda c: -c["score"])
    return candidates[:MAX_CANDIDATES], bh_pass


MIN_WIN_EVENTS = 5


def _tail(r: dict[str, Any]) -> str:
    """A maker's loss is the token paying out (r = −1). With fewer than MIN_WIN_EVENTS events
    in which that happened, the cluster bootstrap has (almost) no losses to resample and its
    interval says nothing about the tail: the cell is flagged, whatever the rule says."""
    return (
        f"no tail observed ({r['n_win_events']} loss events)"
        if r["n_win_events"] < MIN_WIN_EVENTS
        else ""
    )


def _f(x: float | None, spec: str = "+.3f") -> str:
    return "—" if x is None else format(x, spec)


def write_report(
    out: Path,
    months: list[str],
    df: pl.DataFrame,
    cells: pl.DataFrame,
    candidates: list[dict[str, Any]],
    bh_pass: set[str],
    n_register: int,
    resamples: int,
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    cells.write_parquet(out / "resting.parquet")
    prim = cells.filter(pl.col("family") == "primary")
    sec = cells.filter(pl.col("family") == "secondary")
    ultra = df.filter(pl.col("duration_class") == "ultra_short")
    lines = [
        "# S1b — resting-order return at taker-BUY prints: discovery report",
        "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')} by `tools/research/s1b_resting_order.py`.",
        "",
        "**Disclaimer first.** Every number is in-sample on the discovery set D, which also",
        "motivated the family (registration.yaml, `motivation_from_data`). A cell that passes the",
        "selection rule is a *candidate* for the same measurement on C1 and C2, not a finding.",
        "The maker here is a counterfactual resting order at the taker's print price: it assumes",
        "the maker was at the front of the queue and pays no fee; liquidity-rewards payments are",
        "not modelled. Nothing below is a trading result.",
        "",
        f"Months: {', '.join(months)}. Taker-BUY prints in fill windows: {df.height:,} "
        f"({df.filter(pl.col('duration_class') != 'ultra_short').height:,} outside ultra_short markets; "
        f"{ultra.height:,} on ultra_short markets, reported separately, never pooled).",
        f"Register N (primary cells, `s1b_resting_order:cells`): {n_register}. "
        f"Cluster bootstrap: {resamples:,} resamples by event and by UTC day; the wider interval is shown. "
        f"Secondary cells computed and shown: {sec.filter(pl.col('n') > 0).height} of {sec.height}.",
        "",
        "## Verdict",
        "",
    ]
    if candidates:
        flagged = sum(1 for c in candidates if _tail(c))
        lines.append(
            f"**{len(candidates)} candidate cell(s)** cleared the registered rule; {flagged} of them "
            f"with fewer than {MIN_WIN_EVENTS} loss events (a maker loses when the token pays out), "
            "where the bootstrap interval cannot see the tail — flagged in the last column, not removed."
        )
        lines.append("")
        lines.append(
            "| # | Category | Bucket | Offset | n | events | wins (events) | r_maker | 90% CI (key) | size-weighted | rebate 25% | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | BH 10% (descr.) | tail |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(candidates, start=1):
            key = f"{c['category']}|{c['bucket']}|{c['offset']}"
            lines.append(
                f"| {i} | {c['category']} | {c['bucket']} | {c['offset']} | {c['n']:,} | {c['n_events']} | {c['n_wins']} ({c['n_win_events']}) | {_f(c['r_maker'])} | "
                f"{_f(c['ci_lo'])} to {_f(c['ci_hi'])} ({c['cluster_key']}) | {_f(c['r_size_weighted'])} | {_f(c['r_rebate'])} | "
                f"{_f(c['as5'])} | {_f(c['as30'])} | {_f(c['mde_80'], '.3f')} | {c['pos_months']}/{c['seen_months']} | "
                f"${c['capacity_1000']:,.0f} | {'pass' if key in bh_pass else 'no'} | {_tail(c)} |"
            )
    else:
        lines.append(
            f"**Candidates: none.** No primary cell cleared the registered rule "
            f"({len(bh_pass)} of {n_register} primary cells would pass BH at 10% FDR, a descriptive count)."
        )
    lines.append("")
    lines.append("## Primary family — every cell with data (n ≥ 30)")
    lines.append("")
    lines.append(
        "| Category | Bucket | Offset | n | events | wins (events) | r_maker | 90% CI | size-wtd | rebate 25% | AS 5m (cov.) | AS 30m (cov.) | MDE@80% | months + | capacity | status |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in (
        prim.filter(pl.col("n") >= 30).sort(["offset", "bucket", "category"]).iter_rows(named=True)
    ):
        is_cand = any(
            c["category"] == r["category"]
            and c["bucket"] == r["bucket"]
            and c["offset"] == r["offset"]
            for c in candidates
        )
        status = (
            "candidate"
            if is_cand
            else "underpowered"
            if r["n"] < MIN_N_POWER
            else "below size bar"
            if r["n"] < MIN_N or r["n_events"] < MIN_EVENTS
            else "CI above zero, other rule failed"
            if r["ci_lo"] > 0
            else "no effect detected"
        )
        lines.append(
            f"| {r['category']} | {r['bucket']} | {r['offset']} | {r['n']:,} | {r['n_events']} | {r['n_wins']} ({r['n_win_events']}) | {_f(r['r_maker'])} | "
            f"{_f(r['ci_lo'])} to {_f(r['ci_hi'])} | {_f(r['r_size_weighted'])} | {_f(r['r_rebate'])} | "
            f"{_f(r['as5'])} ({r['as5_coverage']:.0%}) | {_f(r['as30'])} ({r['as30_coverage']:.0%}) | {_f(r['mde_80'], '.3f')} | "
            f"{r['pos_months']}/{r['seen_months']} | ${r['capacity_1000']:,.0f} | {status}{'; ' + _tail(r) if _tail(r) else ''} |"
        )
    n_empty = prim.filter(pl.col("n") < 30).height
    lines.append("")
    lines.append(
        f"{n_empty} of {prim.height} primary cells have fewer than 30 prints and are not listed."
    )
    lines.append("")
    lines.append("## Pooled view (primary family, by bucket and by offset)")
    lines.append("")
    lines.append("| Slice | n | r_maker (equal) | size-weighted | rebate 25% | AS 5m | AS 30m |")
    lines.append("|---|---|---|---|---|---|---|")
    pf = df.filter(
        (pl.col("duration_class") != "ultra_short")
        & pl.col("bucket").is_in(PRIMARY_BUCKETS)
        & pl.col("offset").is_in(PRIMARY_OFFSETS)
    )
    for name, grp in [("bucket", PRIMARY_BUCKETS), ("offset", PRIMARY_OFFSETS)]:
        for v in grp:
            s = pf.filter(pl.col(name) == v)
            if not s.height:
                continue
            w = s["maker_dollars"].to_numpy()
            lines.append(
                f"| {name} {v} | {s.height:,} | {_f(_mean(s['r']))} | "
                f"{_f(float(np.average(s['r'].to_numpy(), weights=w)) if w.sum() > 0 else None)} | "
                f"{_f(_mean(s['r_rebate']))} | {_f(_mean(s['as5']))} | {_f(_mean(s['as30']))} |"
            )
    if pf.height:
        w = pf["maker_dollars"].to_numpy()
        lines.append(
            f"| **all primary** | {pf.height:,} | {_f(_mean(pf['r']))} | "
            f"{_f(float(np.average(pf['r'].to_numpy(), weights=w)))} | {_f(_mean(pf['r_rebate']))} | "
            f"{_f(_mean(pf['as5']))} | {_f(_mean(pf['as30']))} |"
        )
    lines.append("")
    lines.append("## Secondary family (reported, never selected) — cells with n ≥ 100")
    lines.append("")
    lines.append(
        "| Category | Bucket | Offset | n | r_maker | 90% CI | AS 30m | MDE@80% | months + |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in (
        sec.filter(pl.col("n") >= 100).sort(["offset", "bucket", "category"]).iter_rows(named=True)
    ):
        lines.append(
            f"| {r['category']} | {r['bucket']} | {r['offset']} | {r['n']:,} | {_f(r['r_maker'])} | "
            f"{_f(r['ci_lo'])} to {_f(r['ci_hi'])} | {_f(r['as30'])} | {_f(r['mde_80'], '.3f')} | {r['pos_months']}/{r['seen_months']} |"
        )
    if ultra.height:
        lines.append("")
        lines.append("## ultra_short markets (excluded from every cell)")
        lines.append("")
        lines.append(
            f"{ultra.height:,} prints; r_maker equal-weight {_f(_mean(ultra['r']))}, AS 30m {_f(_mean(ultra['as30']))}."
        )
    (out / "report.md").write_text("\n".join(lines) + "\n")
    summary = {
        "months": months,
        "prints": df.height,
        "prints_primary_family": pf.height,
        "n_register": n_register,
        "bh_pass_descriptive": sorted(bh_pass),
        "candidates": candidates,
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
    p.add_argument("--out", default=str(STUDY / "report"))
    p.add_argument("--resamples", type=int, default=10_000)
    args = ap.parse_args()

    registration = yaml.safe_load((STUDY / "registration.yaml").read_text())
    n_register = family_counts().get("s1b_resting_order:cells", 0)
    expected = len(PRIMARY_BUCKETS) * len(PRIMARY_OFFSETS) * len(CATEGORIES)
    assert n_register == expected, (
        f"register has {n_register} S1b cells; expected {expected} "
        f"(registration: {registration['primary_cells']}); register it first"
    )
    print(f"loading taker-BUY prints for {len(args.months)} month(s)", flush=True)
    df = load(args.months)
    print(
        f"{df.height:,} prints; computing {len(CATEGORIES) * len(BUCKETS) * len(OFFSETS)} cells",
        flush=True,
    )
    cells = cell_table(df, args.months, args.resamples)
    candidates, bh_pass = select_candidates(cells, n_register)
    path = write_report(
        Path(args.out), args.months, df, cells, candidates, bh_pass, n_register, args.resamples
    )
    print(f"report → {path}; candidates: {len(candidates)}; BH (descriptive): {len(bh_pass)}")


if __name__ == "__main__":
    main()
