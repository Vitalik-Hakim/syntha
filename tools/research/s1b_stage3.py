"""S1b stage-3 out-of-sample confirmation of one registered cell (R-0793):
crypto markets, taker-BUY prints at 3–10¢ in the registered −15m window, maker (seller) side.

The cell definition, fill model, metric, bar, size requirement and bootstrap come from
research/studies/s1b_resting_order/registration.yaml and its confirmation criteria (R-0946);
this tool changes none of them. Data: crypto-category markets whose scheduled end falls in a
date range and that are resolved, fetched directly from Gamma / the Data API / the CLOB with
the ingestion package's clients into data/s1b-stage3/ (raw JSON records plus Parquet). No
database, no normalized layer.

  manifest  --start 2026-09-01 --end 2026-10-12      listing → classification → manifest.json + markets.parquet
  fetch                                                resolutions, trades (final window), 1-minute prices → parquet
  replay    --january data/s1b-stage3/release/s1b_prints.parquet   metric, bootstrap, set rule, report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from s1_calibration import BUCKETS, _bucket_expr, cluster_ci
from s1b_resting_order import (
    CONFIRM_MIN_N,
    MARK_FALLBACK_H,
    MIN_WIN_EVENTS,
    PRESETS,
    SEED,
    _f,
    cell_stats,
    derive,
    set_rule,
)

from syntha_ingestion import polymarket as pm
from syntha_ingestion.census import (
    FINE_WINDOW_S,
    _series_for,
    payout_for,
    print_walk,
)
from syntha_ingestion.http import ApiClient, FetchError
from syntha_ingestion.parse import (
    TAG_CATEGORIES,
    deadline_type,
    duration_class,
    fee_category,
    json_list,
    open_ts,
    parse_ts,
    resolve_market,
)
from syntha_ingestion.raw_store import RawStore

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "s1b-stage3"
STUDY = ROOT / "research" / "studies" / "s1b_resting_order"

# The registered cell (R-0793) and the registered window (unit_of_observation, offsets).
CELL = {"category": "crypto", "bucket": "[0.03,0.10)", "offset": "-15m"}
OFFSET_S = 15 * 60  # the −15m census offset: t0 = scheduled end − 15 min
WINDOW_S = 10 * 60  # fill window [t0, t0 + 10 min) — registration frozen_parameters
TRADES_LOOKBACK_S = OFFSET_S + 3600  # prints back to one hour before the window, for staleness
TRADES_MAX_PAGES = 10

# Polymarket's fee and maker-rebate schedule as published on 2026-10-12 (cited in the manifest).
FEE_DOCS: dict[str, Any] = {
    "fetched_on": "2026-10-12",
    "pages": {
        "fees": "https://docs.polymarket.com/trading/fees.md",
        "maker_rebates": "https://docs.polymarket.com/programs/maker-rebates.md",
        "help_article": "https://help.polymarket.com/en/articles/13364478-trading-fees",
    },
    "formula": "fee = C × feeRate × p × (1 − p); C = shares, p = share price; rounded to 5 decimals",
    "crypto_taker_rate": 0.07,
    "exponent": 1,
    "maker_fee": 0.0,  # "Makers are never charged fees."
    "crypto_maker_rebate_share": 0.20,  # "Crypto makers receive 20% of taker fees", paid daily
    "rebate_mechanics": (
        "daily pool per market = share × that market's taker fees, split among makers pro rata "
        "by fee-equivalent volume; a per-fill rebate of share × the taker's fee is therefore an "
        "upper bound (a lone maker); rebate share 'at the sole discretion of Polymarket'"
    ),
}
CRYPTO_RATE = float(FEE_DOCS["crypto_taker_rate"])
REBATE_CURRENT = float(FEE_DOCS["crypto_maker_rebate_share"])


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _gather(
    jobs: list[Callable[[], Awaitable[Any]]], concurrency: int, label: str
) -> list[Any]:
    sem = asyncio.Semaphore(concurrency)
    done = 0
    results: list[Any] = [None] * len(jobs)

    async def run(i: int, job: Callable[[], Awaitable[Any]]) -> None:
        nonlocal done
        async with sem:
            try:
                results[i] = await job()
            except FetchError as exc:
                results[i] = {"error": f"{exc.status}: {str(exc)[:120]}"}
            done += 1
            if done % 200 == 0 or done == len(jobs):
                print(f"  {label}: {done}/{len(jobs)}", flush=True)

    await asyncio.gather(*(run(i, j) for i, j in enumerate(jobs)))
    return results


# ------------------------------------------------------------------ manifest


KNOWN_CATEGORIES = {
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
}


def category_of(fee_type: str | None, tags: list[str] | None) -> tuple[str | None, str]:
    """(category, how). The registration's rule is "feeType → category, else event tags".
    Fee types that name no category (`zero_fees`, seen on 2026 micro-markets) fall through to
    the tags, as a missing feeType does; `tags is None` means the event was not fetched."""
    if fee_type:
        c = fee_category(fee_type)
        if c in KNOWN_CATEGORIES:
            return c, "feeType"
    if tags is None:
        return None, "needs_event"
    lowered = {t.lower() for t in tags}
    for cat in TAG_CATEGORIES:
        if cat in lowered:
            return cat, "tags"
    return "other", "tags_none"


# A 2026 crypto series the frozen classifier did not exist for: "<coin> above <strike> on <date>,
# <hour> ET?" — one strike per hour, created ~79 minutes before the end. The question names a
# point in time, not a window, and end − open is 79 minutes, so the registered ultra_short rule
# (question window ≤ 60 min or end − open ≤ 60 min) leaves it in the cell as `intraday`. It is
# flagged here, never reclassified, so the report can show the cell with and without it.
HOURLY_STRIKE = re.compile(
    r"^.+ above [\d,\.]+ on (January|February|March|April|May|June|July|August|September|"
    r"October|November|December) \d{1,2}, \d{1,2}(:\d{2})?\s?(AM|PM) ET\?$",
    re.IGNORECASE,
)


def _compact(m: dict[str, Any]) -> dict[str, Any] | None:
    """The fields the manifest and the replay need from a Gamma listing object."""
    cid = m.get("conditionId")
    start_dt, end_dt = open_ts(m), parse_ts(m.get("endDate"))
    tokens = [str(t) for t in json_list(m.get("clobTokenIds"))]
    if not cid or start_dt is None or end_dt is None or not tokens:
        return None
    events = m.get("events") or []
    sched = m.get("feeSchedule") if m.get("feesEnabled") else None
    return {
        "market_id": str(m.get("id")),
        "condition_id": str(cid),
        "event_id": str(events[0]["id"]) if events and events[0].get("id") is not None else None,
        "question": m.get("question"),
        "fee_type": m.get("feeType"),
        "duration_class": duration_class(start_dt, end_dt, m.get("question")),
        "deadline_type": deadline_type(m.get("question")),
        "tokens": tokens,
        "open_ts": int(start_dt.timestamp()),
        "end_ts": int(end_dt.timestamp()),
        "closed_time": m.get("closedTime"),
        "fees_enabled": bool(m.get("feesEnabled")),
        "fee_rate": None if not sched else float(sched.get("rate") or 0),
        "fee_exponent": None if not sched else sched.get("exponent"),
        "neg_risk": bool(m.get("negRisk")),
        "hourly_strike_series": bool(HOURLY_STRIKE.match(m.get("question") or "")),
    }


def _saved_listing(raw: RawStore) -> tuple[Iterator[dict[str, Any]], dict[str, Any] | None]:
    """Markets from the keyset pages already saved under raw/, streamed one page at a time, and
    the resume point (next cursor, next page, the cursor that fetched the last page)."""
    recs = [r for r in raw.records(pm.GAMMA_MARKETS_KEYSET) if r.status == 200]
    recs.sort(key=lambda r: (r.fetched_at, int(r.unit["page"])))
    resume = None
    if recs:
        last = recs[-1]
        resume = {
            "start_cursor": last.json().get("next_cursor"),
            "start_page": int(last.unit["page"]) + 1,
            "previous_cursor": last.params.get("after_cursor"),
        }

    def gen() -> Iterator[dict[str, Any]]:
        for r in recs:
            yield from r.json().get("markets") or []

    return gen(), resume


async def build_manifest(
    start: str, end: str, out: Path, rps: float, concurrency: int
) -> dict[str, Any]:
    raw = RawStore(out / "raw")
    end_date_min, end_date_max = f"{start}T00:00:00Z", f"{end}T23:59:59Z"
    compact: dict[str, dict[str, Any]] = {}
    counts = {"listed": 0, "unclassifiable": 0, "ultra_short": 0, "duplicates": 0}
    by_fee_type: dict[str, int] = {}

    def take(m: dict[str, Any]) -> None:
        counts["listed"] += 1
        c = _compact(m)
        if c is None:
            counts["unclassifiable"] += 1
            return
        if c["condition_id"] in compact:
            counts["duplicates"] += 1
            return
        by_fee_type[c["fee_type"] or "none"] = by_fee_type.get(c["fee_type"] or "none", 0) + 1
        if c["duration_class"] == "ultra_short":
            counts["ultra_short"] += 1  # excluded from the cell whatever the category
            compact[c["condition_id"]] = {
                "condition_id": c["condition_id"],
                "ultra_short": True,
                "fee_type": c["fee_type"],
            }
            return
        compact[c["condition_id"]] = c

    saved, resume = _saved_listing(raw)
    for m in saved:
        take(m)
    print(
        f"saved pages: {counts['listed']:,} markets read; resume at page {resume['start_page'] if resume else 0}",
        flush=True,
    )
    async with ApiClient(raw, requests_per_second=rps) as client:
        kwargs: dict[str, Any] = {}
        if resume and resume["start_cursor"]:
            kwargs = {
                "start_cursor": resume["start_cursor"],
                "start_page": resume["start_page"],
                "previous_cursor": resume["previous_cursor"],
            }
        if not resume or resume["start_cursor"]:
            n0 = counts["listed"]
            async for m in pm.list_markets(
                client, end_date_min=end_date_min, end_date_max=end_date_max, closed=True, **kwargs
            ):
                take(m)
                if (counts["listed"] - n0) % 50_000 == 0:
                    print(f"  listing: {counts['listed']:,} markets", flush=True)
        full = [c for c in compact.values() if not c.get("ultra_short")]
        print(
            f"listing: {counts['listed']:,} rows, {len(compact):,} distinct markets, "
            f"{counts['ultra_short']:,} ultra_short, {len(full):,} longer than an hour",
            flush=True,
        )
        # category: feeType when it names a category; otherwise the event's tags (fetched only
        # for the non-ultra_short markets that need them)
        need_event = sorted(
            {
                c["event_id"]
                for c in full
                if c["event_id"] and category_of(c["fee_type"], None)[1] == "needs_event"
            }
        )
        events: dict[str, dict[str, Any]] = {}

        def event_job(eid: str) -> Callable[[], Awaitable[Any]]:
            async def job() -> Any:
                events[eid] = await pm.fetch_event(client, eid)
                return True

            return job

        print(f"events to fetch for tag-based category: {len(need_event):,}", flush=True)
        await _gather([event_job(e) for e in need_event], concurrency, "events")
        requests = client.stats.requests
    rows = []
    for c in full:
        ev = events.get(c["event_id"]) if c["event_id"] else None
        tags = (
            [str(t.get("slug") or t.get("label") or "") for t in (ev or {}).get("tags") or []]
            if ev is not None
            else None
        )
        category, how = category_of(c["fee_type"], tags)
        if category is None:
            category, how = "other", "no_event"
        if category != "crypto":
            continue
        in_window = c["end_ts"] - OFFSET_S >= c["open_ts"]
        n_markets = len((ev or {}).get("markets") or []) if ev is not None else None
        rows.append(
            c
            | {
                "category": category,
                "category_from": how,
                "structure": None
                if n_markets is None
                else ("sibling" if n_markets >= 3 else "binary"),
                "in_cell": in_window,
                "exclusion_reason": ""
                if in_window
                else "no -15m observation (opened inside the window)",
            }
        )
    df = pl.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out / "markets.parquet")
    registration = yaml.safe_load((STUDY / "registration.yaml").read_text())
    in_cell = df.filter(pl.col("in_cell"))
    ultra_crypto_fee = sum(
        1
        for c in compact.values()
        if c.get("ultra_short") and fee_category(c["fee_type"]) == "crypto"
    )
    manifest = {
        "created_at": _now(),
        "study": "s1b_resting_order",
        "register_rows": {"cell": "R-0793", "study": "R-0769", "confirmation_criteria": "R-0946"},
        "cell": CELL,
        "registered_definitions_verbatim": {
            "unit_of_observation": registration["unit_of_observation"],
            "estimand": registration["estimand"],
            "adverse_selection": registration["adverse_selection"],
            "exclusions": registration["exclusions"],
            "frozen_parameters": registration["frozen_parameters"],
            "confirmation_criteria": registration["confirmation_criteria"],
        },
        "confirmation_set": {
            "definition": (
                "crypto-category markets whose scheduled end (Gamma endDate) falls in the range and "
                "that are resolved; category = the fee type's category when it names one, else the "
                "event's tags (the registration's rule; the 2026 `zero_fees` type names no category "
                "and falls through to tags); the −15m observation requires end − 15 min ≥ open; "
                "ultra_short markets (question window ≤ 60 min or end − open ≤ 60 min) are excluded "
                "as the registration requires and are not classified by category"
            ),
            "range_inclusive": {"start": start, "end": end},
            "gamma_filter": {
                "closed": True,
                "end_date_min": end_date_min,
                "end_date_max": end_date_max,
            },
            "note": (
                "Gamma filters by scheduled end, not resolution time; resolution timestamps come "
                "from /v2/resolutions after the fetch and are recorded per market"
            ),
            "counts": {
                "listing_rows": counts["listed"],
                "distinct_markets": len(compact),
                "duplicates_across_pages": counts["duplicates"],
                "unclassifiable_no_tokens_or_times": counts["unclassifiable"],
                "ultra_short_all_categories": counts["ultra_short"],
                "ultra_short_with_crypto_fee_type": ultra_crypto_fee,
                "longer_than_an_hour": len(full),
                "events_fetched_for_tags": len(events),
                "crypto_markets": df.height,
                "crypto_in_cell": in_cell.height,
                "crypto_by_category_source": dict(df.group_by("category_from").len().iter_rows())
                if df.height
                else {},
                "crypto_in_cell_hourly_strike_series": int(
                    in_cell.filter(pl.col("hourly_strike_series")).height
                )
                if df.height
                else 0,
                "crypto_in_cell_by_duration_class": dict(
                    in_cell.group_by("duration_class").len().iter_rows()
                )
                if df.height
                else {},
                "crypto_by_fee_type": dict(df.group_by("fee_type").len().iter_rows())
                if df.height
                else {},
                "listing_fee_types": by_fee_type,
                "api_requests_this_run": requests,
            },
            "markets_in_cell": [
                {
                    "condition_id": r["condition_id"],
                    "question": r["question"],
                    "end_ts": r["end_ts"],
                }
                for r in in_cell.sort("end_ts").iter_rows(named=True)
            ],
        },
        "fee_schedule_today": FEE_DOCS,
        "data_availability": {
            "best_bid_ask_final_15_min": (
                "not available historically from any public Polymarket endpoint (the CLOB /book "
                "is live-only; the recorder's L2 frames were deleted at the pause); the registered "
                "fill model uses taker-BUY prints, not quotes; the CLOB /prices-history at "
                "fidelity 1 (the sampled price series) is fetched for the last 6 hours as the "
                "registered mark source"
            ),
            "prints": "/v2/trades newest-first, stopped one hour before the window, ≤ 10 pages",
            "resolutions": "/v2/resolutions in batches of 20",
        },
        "data_seen_before_this_fetch": (
            "none of these markets: all registered S1b work used census months up to 2026-08 "
            "and 2026-01 (register rows R-0769, R-0793, R-0946)"
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str) + "\n")
    print(
        f"manifest → {out / 'manifest.json'}: {df.height:,} crypto markets, {in_cell.height:,} in the cell",
        flush=True,
    )
    return manifest


# ------------------------------------------------------------------ fetch


async def fetch(
    out: Path, rps: float, concurrency: int, max_pages: int, prices_rps: float, subset: str
) -> None:
    """Resolutions, then trade walks (Data API bucket) concurrently with 1-minute prices (CLOB
    bucket, documented at 100/s against 30/s for trades — docs/DATA_NOTES.md)."""
    raw = RawStore(out / "raw")
    df = pl.read_parquet(out / "markets.parquet").filter(pl.col("in_cell"))
    if subset == "daily":
        df = df.filter(~pl.col("hourly_strike_series"))
    elif subset == "hourly":
        df = df.filter(pl.col("hourly_strike_series"))
    print(f"fetch subset={subset}: {df.height:,} markets", flush=True)
    conds = sorted(df["condition_id"].to_list())
    async with (
        ApiClient(raw, requests_per_second=rps) as client,
        ApiClient(raw, requests_per_second=prices_rps) as prices,
    ):

        def res_job(batch: list[str]) -> Callable[[], Awaitable[Any]]:
            async def job() -> Any:
                return len(await pm.fetch_resolutions(client, batch))

            return job

        await _gather(
            [res_job(b) for b in pm.resolution_batches(conds)], concurrency, "resolutions"
        )

        def trades_job(cid: str, end_s: int) -> Callable[[], Awaitable[Any]]:
            async def job() -> Any:
                return await pm.walk_trades_window(
                    client, cid, stop_before_ts=end_s - TRADES_LOOKBACK_S, max_pages=max_pages
                )

            return job

        def price_job(token: str, a: int, b: int) -> Callable[[], Awaitable[Any]]:
            async def job() -> Any:
                return await pm.fetch_price_history(prices, token, a, b, 1)

            return job

        pjobs = []
        for r in df.iter_rows(named=True):
            a, b = max(r["open_ts"], r["end_ts"] - FINE_WINDOW_S), r["end_ts"]
            for t in r["tokens"]:
                if b > a:
                    pjobs.append(price_job(str(t), a, b))
        info, points = await asyncio.gather(
            _gather(
                [trades_job(r["condition_id"], r["end_ts"]) for r in df.iter_rows(named=True)],
                concurrency,
                "trades",
            ),
            _gather(pjobs, max(concurrency, int(prices_rps)), "prices"),
        )
        print(
            f"requests={client.stats.requests + prices.stats.requests} "
            f"retries={client.stats.retries + prices.stats.retries} "
            f"failures={client.stats.failures + prices.stats.failures}",
            flush=True,
        )
    walks = [i for i in info if isinstance(i, dict) and "error" not in i]
    summary = {
        "fetched_at": _now(),
        "markets": df.height,
        "trade_walks": len(walks),
        "trade_walk_errors": len(info) - len(walks),
        "truncated_walks": sum(1 for i in walks if i.get("truncated")),
        "prints_fetched": sum(int(i.get("prints") or 0) for i in walks),
        "price_requests": len(pjobs),
        "price_points": sum(int(p) for p in points if isinstance(p, int)),
    }
    _to_parquet(out, pl.read_parquet(out / "markets.parquet").filter(pl.col("in_cell")))
    (out / f"fetch_summary_{subset}.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


def _to_parquet(out: Path, df: pl.DataFrame) -> None:
    """Raw JSON records → prints.parquet, bars.parquet, resolutions.parquet."""
    raw = RawStore(out / "raw")
    by_cond: dict[str, list[Any]] = {}
    for rec in raw.records(pm.DATA_V2_TRADES):
        if rec.status == 200:
            by_cond.setdefault(str(rec.unit["condition"]), []).append(rec)
    by_token: dict[str, list[Any]] = {}
    for rec in raw.records(pm.CLOB_PRICES_HISTORY):
        if rec.status == 200:
            by_token.setdefault(str(rec.unit["token"]), []).append(rec)
    v2: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.DATA_V2_RESOLUTIONS):
        if rec.status != 200:
            continue
        for row in rec.json().get("data") or []:
            key = row.get("condition") or row.get("conditionId") or row.get("condition_id")
            if key:
                v2[str(key)] = row
    listing: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.GAMMA_MARKETS_KEYSET):
        if rec.status == 200:
            for m in rec.json().get("markets") or []:
                if m.get("conditionId"):
                    listing[str(m["conditionId"])] = m
    prints, bars, res = [], [], []
    for r in df.iter_rows(named=True):
        cid = r["condition_id"]
        walk = print_walk(by_cond.get(cid, []), r["end_ts"])
        for p in walk.prints:
            prints.append((cid, p.token, p.ts, p.price, p.size, p.side))
        for t in r["tokens"]:
            for ts, price in _series_for(by_token.get(str(t), [])):
                bars.append((cid, str(t), ts, price))
        g = listing.get(cid)
        rr = resolve_market(g, v2.get(cid)) if g is not None else None
        res.append(
            (
                cid,
                rr.resolution if rr else "UNRESOLVED",
                rr.resolved_at if rr else None,
                rr.anomaly if rr else "not in listing",
                walk.complete,
                walk.truncated,
                walk.earliest,
            )
        )
    pl.DataFrame(
        prints,
        schema={
            "condition_id": pl.Utf8,
            "clob_token_id": pl.Utf8,
            "ts": pl.Int64,
            "price": pl.Float64,
            "size": pl.Float64,
            "side": pl.Int8,
        },
        orient="row",
    ).write_parquet(out / "prints.parquet")
    pl.DataFrame(
        bars,
        schema={
            "condition_id": pl.Utf8,
            "clob_token_id": pl.Utf8,
            "ts": pl.Int64,
            "price": pl.Float64,
        },
        orient="row",
    ).write_parquet(out / "bars.parquet")
    pl.DataFrame(
        res,
        schema={
            "condition_id": pl.Utf8,
            "resolution": pl.Utf8,
            "resolved_at": pl.Datetime("us", "UTC"),
            "anomaly": pl.Utf8,
            "walk_complete": pl.Boolean,
            "walk_truncated": pl.Boolean,
            "walk_earliest": pl.Int64,
        },
        orient="row",
    ).write_parquet(out / "resolutions.parquet")


# ------------------------------------------------------------------ replay


def _mark(
    bar_at: dict[int, float],
    print_ts: list[int],
    print_px: list[float],
    t_p: int,
    delta_min: int,
) -> tuple[float | None, int | None, float | None]:
    """(bar price at the mark minute, its staleness in minutes, next print price at/after the
    mark within 24 h) — the registered adverse-selection inputs."""
    import bisect

    mark_ts = t_p + delta_min * 60
    minute = (t_p // 60) * 60 + delta_min * 60
    bar = bar_at.get(minute)
    stale = None
    i = bisect.bisect_right(print_ts, minute)
    if i > 0:
        stale = (minute - print_ts[i - 1]) // 60
    j = bisect.bisect_left(print_ts, mark_ts)
    nxt = None
    if j < len(print_ts) and print_ts[j] <= mark_ts + MARK_FALLBACK_H * 3600:
        nxt = print_px[j]
    return bar, stale, nxt


def fills_frame(out: Path) -> tuple[pl.DataFrame, dict[str, Any]]:
    """One row per taker-BUY print in the registered window on an in-cell market, with payout
    and marks; the bucket filter is applied by the caller so the counts can be reported."""
    markets = pl.read_parquet(out / "markets.parquet").filter(pl.col("in_cell"))
    prints = pl.read_parquet(out / "prints.parquet")
    bars = pl.read_parquet(out / "bars.parquet")
    res = pl.read_parquet(out / "resolutions.parquet")
    res_map = {r["condition_id"]: r for r in res.iter_rows(named=True)}
    bars_by_token: dict[str, dict[int, float]] = {}
    for r in bars.iter_rows(named=True):
        bars_by_token.setdefault(r["clob_token_id"], {})[(r["ts"] // 60) * 60] = r["price"]
    prints_by_token: dict[str, tuple[list[int], list[float]]] = {}
    for tok, grp in prints.sort("ts").group_by("clob_token_id"):
        prints_by_token[str(tok[0])] = (grp["ts"].to_list(), grp["price"].to_list())
    rows = []
    counts = {"markets_in_cell": markets.height, "unresolved_or_invalid": 0, "no_prints": 0}
    for m in markets.iter_rows(named=True):
        cid = m["condition_id"]
        rr = res_map.get(cid)
        resolution = rr["resolution"] if rr else "UNRESOLVED"
        if resolution in ("UNRESOLVED", "INVALID") or rr is None:
            counts["unresolved_or_invalid"] += 1
            continue
        t0 = m["end_ts"] - OFFSET_S
        sub = prints.filter(
            (pl.col("condition_id") == cid)
            & (pl.col("side") == 1)
            & (pl.col("ts") >= t0)
            & (pl.col("ts") < t0 + WINDOW_S)
        )
        if sub.height == 0:
            counts["no_prints"] += 1
            continue
        tokens = [str(t) for t in m["tokens"]]
        for p in sub.iter_rows(named=True):
            tok = p["clob_token_id"]
            if tok not in tokens:
                continue
            payout = payout_for(resolution, tokens.index(tok))
            if payout is None:
                continue
            pts, pxs = prints_by_token.get(tok, ([], []))
            bar5, stale5, next5 = _mark(bars_by_token.get(tok, {}), pts, pxs, p["ts"], 5)
            bar30, stale30, next30 = _mark(bars_by_token.get(tok, {}), pts, pxs, p["ts"], 30)
            rows.append(
                {
                    "condition_id": cid,
                    "clob_token_id": tok,
                    "event_id": m["event_id"] or cid,
                    "offset": "-15m",
                    "t0": datetime.fromtimestamp(t0, UTC),
                    "payout": float(payout),
                    "category": "crypto",
                    "duration_class": m["duration_class"],
                    "deadline_type": m["deadline_type"],
                    "t_p": datetime.fromtimestamp(p["ts"], UTC),
                    "p": p["price"],
                    "size": p["size"],
                    "bar5": bar5,
                    "stale5": stale5,
                    "bar30": bar30,
                    "stale30": stale30,
                    "next5": next5,
                    "next30": next30,
                    "month": datetime.fromtimestamp(m["end_ts"], UTC).strftime("%Y-%m"),
                    "day": datetime.fromtimestamp(p["ts"], UTC).strftime("%Y-%m-%d"),
                    "resolved_at": rr["resolved_at"],
                    "source": "stage3",
                    "hourly_strike_series": bool(m["hourly_strike_series"]),
                }
            )
    df = pl.DataFrame(rows) if rows else pl.DataFrame()
    return df, counts


def prepare(df: pl.DataFrame) -> pl.DataFrame:
    """The columns `derive`/`cell_stats` expect; the taker fee under today's crypto schedule."""
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
    return df.filter((pl.col("p") > 0.01) & (pl.col("p") < 0.99)).with_columns(
        _bucket_expr("p").alias("bucket"),
        (CRYPTO_RATE * pl.col("p") * (1 - pl.col("p"))).alias("taker_fee_per_share"),
        mark5.alias("mark5"),
        mark30.alias("mark30"),
        (pl.col("bar5").is_not_null() & (pl.col("stale5") <= 5)).alias("mark5_from_bar"),
    )


def january_rows(path: Path) -> pl.DataFrame:
    """The registered C2's January rows for this cell from the pause release's s1b_prints.parquet
    (census-sampled; deadline_type was not stored there)."""
    j = pl.read_parquet(path).filter(
        (pl.col("month") == "2026-01")
        & (pl.col("category") == "crypto")
        & (pl.col("duration_class") != "ultra_short")
        & (pl.col("offset") == "-15m")
        & pl.col("payout").is_not_null()
    )
    return j.with_columns(
        pl.col("t_p").dt.strftime("%Y-%m-%d").alias("day"),
        pl.lit("unknown").alias("deadline_type"),
        pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("resolved_at"),
        pl.lit("census-2026-01").alias("source"),
        pl.lit(False).alias("hourly_strike_series"),
    ).select(
        "condition_id",
        "clob_token_id",
        "event_id",
        "offset",
        "t0",
        "payout",
        "category",
        "duration_class",
        "deadline_type",
        "t_p",
        "p",
        "size",
        "bar5",
        "stale5",
        "bar30",
        "stale30",
        "next5",
        "next30",
        "month",
        "day",
        "resolved_at",
        "source",
        "hourly_strike_series",
    )


def _row(label: str, s: dict[str, Any], rule: str = "") -> str:
    return (
        f"| {label} | {s['n']:,} | {s['n_markets']} | {s['n_events']} | {s['n_win_events']} ({s['n_wins']}) | "
        f"{_f(s['r_maker'])} | {_f(s['ci_lo'])} to {_f(s['ci_hi'])} ({s['cluster_key']}) | "
        f"{_f(s['r_size_weighted'])} | {_f(s['r_event_weighted'])} | {_f(s['as5'])} | {_f(s['as30'])} | "
        f"{_f(s['mde_80'], '.3f')} | {s['pos_months']}/{s['seen_months']} | ${s['capacity_1000']:,.0f} | {rule} |"
    )


def verbatim_definitions() -> list[str]:
    """The register rows and registration passages that fix this confirmation, copied as-is
    from the pause tag's files (the working tree's copies are identical for these rows)."""
    rows = [
        line
        for line in (ROOT / "docs" / "research" / "REGISTER.md").read_text().splitlines()
        if line.startswith(("| R-0769 ", "| R-0793 ", "| R-0946 "))
    ]
    reg = yaml.safe_load((STUDY / "registration.yaml").read_text())
    lines = [
        "## Registered definitions (verbatim, fixed)",
        "",
        "Register rows (`docs/research/REGISTER.md` at tag `pause-2026-09-16`):",
        "",
        "```",
        *rows,
        "```",
        "",
        "`research/studies/s1b_resting_order/registration.yaml`:",
        "",
        "```yaml",
        "unit_of_observation: >",
        *("  " + ln for ln in reg["unit_of_observation"].strip().splitlines()),
        "estimand: >",
        *("  " + ln for ln in reg["estimand"].strip().splitlines()),
        "adverse_selection: >",
        *("  " + ln for ln in reg["adverse_selection"].strip().splitlines()),
        "exclusions:",
        *("  - " + e for e in reg["exclusions"]),
        "frozen_parameters:",
        *(f"  {k}: {v}" for k, v in reg["frozen_parameters"].items()),
        "confirmation_criteria:",
        *(
            "  " + ln
            for ln in yaml.safe_dump(
                reg["confirmation_criteria"], sort_keys=False, allow_unicode=True, width=100
            ).splitlines()
        ),
        "```",
        "",
        "Cell R-0793: category crypto, price bucket [0.03,0.10) on the taker's print price, offset −15m "
        "(window [end − 15 min, end − 5 min)), maker side (the resting order the taker-BUY print hit). "
        "Bar (per_set_rule): n ≥ 300 prints and ≥ 5 losing events, wider cluster 90% CI above zero, "
        "positive in a majority of the set's months, capacity ≥ $50/month at $1,000 with 10% of flow. "
        "Bootstrap: 10,000 resamples by event_id and by UTC day, wider interval; seed 20260916.",
        "",
    ]
    return lines


def replay(out: Path, months: list[str], resamples: int, january: Path | None) -> None:
    raw_df, counts = fills_frame(out)
    assert raw_df.height, "no fills in the window on any in-cell market"
    df = prepare(raw_df)
    cell = df.filter(pl.col("bucket") == CELL["bucket"])
    results: dict[str, Any] = {
        "generated_at": _now(),
        "cell": CELL,
        "months": months,
        "counts": counts
        | {
            "prints_in_window_all_buckets": df.height,
            "prints_in_cell_bucket": cell.height,
            "markets_with_cell_prints": int(cell["condition_id"].n_unique()),
        },
        "fee_schedule_today": FEE_DOCS,
        "presets": {},
    }
    lines = [
        "# S1b stage 3 — out-of-sample confirmation of R-0793 (crypto, [0.03,0.10), −15m, maker side)",
        "",
        f"Generated {results['generated_at']} by `tools/research/s1b_stage3.py replay`. "
        f"Confirmation set: crypto-category markets with scheduled end 2026-09-01 … 2026-10-12 (inclusive), "
        f"resolved, non-ultra_short, with a −15m observation; months counted by scheduled end: {', '.join(months)} "
        "(October is partial: 12 days).",
        "",
        f"Markets in the cell's universe: {counts['markets_in_cell']:,}; dropped as unresolved or invalid: "
        f"{counts['unresolved_or_invalid']:,}; with no taker-BUY print in the window: {counts['no_prints']:,}. "
        f"Taker-BUY prints in the window, all prices: {df.height:,}; in the registered bucket [0.03,0.10): "
        f"{cell.height:,} on {results['counts']['markets_with_cell_prints']:,} markets.",
        "",
        "Fill model as registered (R-0946): realistic = the maker was the other side of the print at "
        "its price p (deciding); conservative = fill one tick worse, q = p − 0.01, which is the "
        "trade-through reading (a resting sell at q is filled only by a later taker-BUY print strictly "
        "above it). Metric r = (q − payout)/(1 − q) per complement dollar; no maker fee "
        f'(docs: "Makers are never charged fees"); rebate rows use today\'s schedule: crypto taker fee '
        f"{CRYPTO_RATE} × p(1 − p) per share, maker rebate {REBATE_CURRENT:.0%} of taker fees "
        f"({FEE_DOCS['pages']['fees']}, {FEE_DOCS['pages']['maker_rebates']}), applied per fill as an upper bound. "
        "Cluster bootstrap by event and by UTC day (10,000 resamples, wider interval shown), as registered; "
        "the by-market interval is added.",
        "",
        "| row | fills | markets | events | losing events (prints) | r_maker | 90% CI (cluster) | size-wtd | event-wtd | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | set rule |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (preset, shift) in enumerate(PRESETS.items()):
        d = derive(cell, shift)
        s = cell_stats(d, months, resamples, SEED + 1_000_000 + 10 + int(shift * 100))
        by_market = cluster_ci(
            d["r"].to_numpy(),
            d["condition_id"].to_numpy(),
            resamples,
            np.random.default_rng([SEED, 3, i]),
        )
        s["ci_market"] = by_market["ci90"]
        rule = set_rule(s) if preset == "realistic" else ""
        results["presets"][preset] = {"rebate": 0.0, **s, "set_rule": rule}
        lines.append(_row(f"{preset}, rebate 0 (registered headline)", s, rule))
        # today's maker rebate as an upper bound per fill
        d20 = d.with_columns(
            (
                pl.col("r") + REBATE_CURRENT * pl.col("taker_fee_per_share") / (1 - pl.col("q"))
            ).alias("r")
        )
        s20 = cell_stats(d20, months, resamples, SEED + 1_000_000 + 10 + int(shift * 100))
        results["presets"][f"{preset}_rebate_{int(REBATE_CURRENT * 100)}"] = {
            "rebate": REBATE_CURRENT,
            **s20,
            "set_rule": set_rule(s20),
        }
        lines.append(
            _row(
                f"{preset}, rebate {REBATE_CURRENT:.0%} (today's schedule, upper bound)",
                s20,
                set_rule(s20),
            )
        )
    # composition: the 2026 hourly strike series (flagged in the manifest, never reclassified)
    d0 = derive(cell, 0.0)
    no_series = d0.filter(~pl.col("hourly_strike_series"))
    only_series = d0.filter(pl.col("hourly_strike_series"))
    if no_series.height:
        s_no = cell_stats(no_series, months, resamples, SEED + 1_000_000 + 10)
        results["sensitivity_without_hourly_strike_series"] = {**s_no, "set_rule": set_rule(s_no)}
        lines.append(
            _row(
                "realistic, rebate 0, without the hourly strike series (sensitivity)",
                s_no,
                set_rule(s_no),
            )
        )
    if only_series.height:
        s_only = cell_stats(only_series, months, resamples, SEED + 1_000_000 + 11)
        results["hourly_strike_series_only"] = {**s_only, "set_rule": set_rule(s_only)}
        lines.append(
            _row("realistic, rebate 0, hourly strike series only (descriptive)", s_only, "")
        )
    r_real = results["presets"]["realistic"]
    lines += [
        "",
        f"By-market 90% CI, realistic, rebate 0: {_f(r_real['ci_market'][0])} to {_f(r_real['ci_market'][1])}.",
        "",
        "## Sub-buckets inside [0.03,0.10) (realistic, rebate 0)",
        "",
        "| price | fills | markets | losing events | r_maker | 90% CI (cluster) |",
        "|---|---|---|---|---|---|",
    ]
    d = derive(cell, 0.0)
    for lo, hi in ((0.03, 0.05), (0.05, 0.07), (0.07, 0.10)):
        part = d.filter((pl.col("p") >= lo) & (pl.col("p") < hi))
        if part.height:
            s = cell_stats(part, months, resamples, SEED + 7)
            lines.append(
                f"| [{lo:.2f},{hi:.2f}) | {s['n']:,} | {s['n_markets']} | {s['n_win_events']} | {_f(s['r_maker'])} | {_f(s['ci_lo'])} to {_f(s['ci_hi'])} |"
            )
    lines += [
        "",
        "## Per month (realistic, rebate 0)",
        "",
        "| month | fills | markets | losing events | r_maker |",
        "|---|---|---|---|---|",
    ]
    for mth in months:
        part = d.filter(pl.col("month") == mth)
        if part.height:
            wins = part.filter(pl.col("payout") >= 0.5)
            lines.append(
                f"| {mth} | {part.height:,} | {part['condition_id'].n_unique()} | {wins['event_id'].n_unique()} | {_f(float(part['r'].mean()))} |"
            )
    # context: the whole window at every price (descriptive)
    d_all = derive(df, 0.0)
    lines += [
        "",
        "## Context: every taker-BUY print in the window, by registered price bucket (realistic, rebate 0; descriptive)",
        "",
        "| bucket | fills | losing events | r_maker |",
        "|---|---|---|---|",
    ]
    for label, _, _ in BUCKETS:
        part = d_all.filter(pl.col("bucket") == label)
        if part.height:
            wins = part.filter(pl.col("payout") >= 0.5)
            lines.append(
                f"| {label} | {part.height:,} | {wins['event_id'].n_unique()} | {_f(float(part['r'].mean()))} |"
            )
    # registered C2 reading: January (census) ∪ this set
    if january is not None and january.exists():
        jan = prepare(january_rows(january))
        jan_cell = jan.filter(pl.col("bucket") == CELL["bucket"])
        pooled = pl.concat(
            [jan_cell.select(cell.columns), cell.select(cell.columns)], how="vertical_relaxed"
        )
        c2_months = ["2026-01", *months]
        results["registered_C2"] = {
            "months": c2_months,
            "january_prints": jan_cell.height,
            "presets": {},
        }
        lines += [
            "",
            "## Registered C2 reading: January 2026 (census sample, from the pause release) ∪ this set",
            "",
            f"January contributes {jan_cell.height:,} prints from the census sample of 2026-01 (a stratified sample "
            "with the census's print-walk truncation); this set is the full population of its range. The two "
            "sources differ in construction; the pooling is what the registration's C2 definition asks for.",
            "",
            "| row | fills | markets | events | losing events (prints) | r_maker | 90% CI (cluster) | size-wtd | event-wtd | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | set rule |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for preset, shift in PRESETS.items():
            s = cell_stats(
                derive(pooled, shift),
                c2_months,
                resamples,
                SEED + 1_000_000 + 10 + int(shift * 100),
            )
            rule = set_rule(s) if preset == "realistic" else ""
            results["registered_C2"]["presets"][preset] = {**s, "set_rule": rule}
            lines.append(_row(f"C2 = Jan ∪ stage 3, {preset}, rebate 0", s, rule))
        c1 = json.loads((STUDY / "report" / "confirmation_C1.json").read_text())
        e1 = next(e for e in c1["results"] if e["cell"] == CELL)
        k1 = e1["presets"]["conservative"]
        k2 = results["registered_C2"]["presets"]["conservative"]
        n_cons = k1["n"] + k2["n"]
        cons_pooled = (
            ((k1["r_maker"] or 0) * k1["n"] + (k2["r_maker"] or 0) * k2["n"]) / n_cons
            if n_cons
            else None
        )
        cons_decisive = (
            n_cons >= CONFIRM_MIN_N and k1["n_win_events"] + k2["n_win_events"] >= MIN_WIN_EVENTS
        )
        r1, r2 = e1["set_rule"], results["registered_C2"]["presets"]["realistic"]["set_rule"]
        cons_negative = cons_decisive and cons_pooled is not None and cons_pooled <= 0
        if r1 == "underpowered":
            v = "underpowered"
        elif r1 == "fail" or r2 == "fail" or cons_negative:
            v = "rejected"
        elif r1 == "pass" and r2 == "pass" and cons_pooled is not None and cons_pooled > 0:
            v = "confirmed"
        elif r1 == "pass" and r2 == "underpowered":
            v = "promising-unconfirmed"
        else:
            v = "underpowered"
        results["registered_verdict"] = {
            "C1": r1,
            "C2_jan_union_stage3": r2,
            "conservative_C1_union_C2": cons_pooled,
            "verdict": v,
            "C2_complete_per_registration": False,
            "note": "the registered C2 lists 2026-10 as a whole month; this set covers it through 2026-10-12",
        }
        lines += [
            "",
            f"Registered verdict with C1 = {r1} (June–August 2026, from `confirmation_C1.json`), C2 = {r2}, "
            f"conservative C1 ∪ C2 = {_f(cons_pooled)}: **{v}** (C2's October is covered through the 12th only, "
            "so the registration's own C2 remains incomplete).",
        ]
    (out / "fills.parquet").parent.mkdir(exist_ok=True)
    df.write_parquet(out / "fills.parquet")
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str) + "\n")
    (ROOT / "reports").mkdir(exist_ok=True)
    head, body = lines[:1], lines[1:]
    (ROOT / "reports" / "s1b-stage3.md").write_text(
        "\n".join([*head, "", *verbatim_definitions(), *body]) + "\n"
    )
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("--start", default="2026-09-01")
    m.add_argument("--end", default="2026-10-12")
    m.add_argument("--rps", type=float, default=20.0)
    m.add_argument("--concurrency", type=int, default=16)
    f = sub.add_parser("fetch")
    f.add_argument("--rps", type=float, default=20.0)
    f.add_argument("--concurrency", type=int, default=16)
    f.add_argument("--max-pages", type=int, default=TRADES_MAX_PAGES)
    f.add_argument("--prices-rps", type=float, default=40.0)
    f.add_argument("--subset", choices=["all", "daily", "hourly"], default="all")
    r = sub.add_parser("replay")
    r.add_argument("--months", nargs="+", default=["2026-09", "2026-10"])
    r.add_argument("--resamples", type=int, default=10_000)
    r.add_argument("--january", type=Path, default=OUT / "release" / "s1b_prints.parquet")
    for p in (m, f, r):
        p.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if args.cmd == "manifest":
        asyncio.run(build_manifest(args.start, args.end, args.out, args.rps, args.concurrency))
    elif args.cmd == "fetch":
        asyncio.run(
            fetch(
                args.out, args.rps, args.concurrency, args.max_pages, args.prices_rps, args.subset
            )
        )
    else:
        replay(args.out, args.months, args.resamples, args.january)


if __name__ == "__main__":
    main()
