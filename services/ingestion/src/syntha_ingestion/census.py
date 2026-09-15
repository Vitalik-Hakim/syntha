"""Calibration census (PRD v3 §4.1, D1): the sparse ingest behind studies S1/S2.

Per calendar month, the sampling frame is every Gamma market scheduled to end in the month
(`closed=true`, keyset listing with the M7 poisoned-cursor routing). The sampler sees only
outcome-independent fields — condition id, fee type, event tags, open and end times, neg-risk
and event membership — and allocates up to `per_stratum` markets per (category, duration class)
uniformly by seed, `ultra_short` capped separately. Per sampled market it fetches hourly prices
for the last 15 days (1-minute for the last 6 hours of short markets), the newest prints back
to 8 days before the end or 10 pages, and the resolution. The build turns raw records into one
observation per (market, token, offset), deterministically, and hashes the result.

Endpoint behaviour used here was verified live on 2026-09-15 (docs/DATA_NOTES.md, "M9 prep").
Nothing here reads a market's volume, liquidity or outcome before the sample is drawn; `payout`
is written from the resolution path only, after the feature columns.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from syntha_contracts.backtest import FeeConfig
from syntha_contracts.enums import FeeMode
from syntha_engine.data import FeeScheduleEntry, MarketTerms
from syntha_engine.execution.fees import FeeTerms, fee_amount, resolve_fee_terms
from syntha_ingestion import polymarket as pm
from syntha_ingestion.config import IngestSettings, psycopg_url
from syntha_ingestion.fetch import Checkpoints, FetchReport, _run_units, saved_listing
from syntha_ingestion.http import ApiClient
from syntha_ingestion.normalize import _dec, _session, _ts, fee_schedule_rows
from syntha_ingestion.parse import (
    deadline_type,
    duration_class,
    fee_category,
    json_list,
    market_category,
    open_ts,
    parse_ts,
    print_key,
    resolve_market,
)
from syntha_ingestion.raw_store import RawRecord, RawStore

log = logging.getLogger(__name__)

CENSUS_DIR = "_census"  # census manifests; kept apart from collection manifests on purpose
OFFSETS_END: tuple[tuple[str, int], ...] = (
    ("-7d", 7 * 86_400),
    ("-3d", 3 * 86_400),
    ("-24h", 86_400),
    ("-6h", 6 * 3_600),
    ("-1h", 3_600),
    ("-15m", 900),
)
OFFSETS_OPEN: tuple[tuple[str, int], ...] = (("+1h", 3_600), ("+24h", 86_400))
PRICE_WINDOW_S = 15 * 86_400  # the CLOB's maximum, verified at fidelity 60 (16 days → error)
FINE_WINDOW_S = 6 * 3_600
FINE_CLASSES = frozenset({"ultra_short", "intraday"})
PRINT_DEPTH_S = 8 * 86_400
PRINT_PAGES_MAX = 10
FILL_WINDOWS: tuple[tuple[str, int], ...] = (("10m", 600), ("30m", 1_800))
LIQ_WINDOW_S = 86_400
PER_STRATUM = 2_000
ULTRA_SHORT_CAP = 500
SIBLING_MIN_MARKETS = 3


def census_name(month: str) -> str:
    return f"census-{month}"


def month_window(month: str) -> tuple[str, str]:
    """`2025-07` → the ISO bounds the Gamma listing takes (end_date_max is the next month's
    start, as for every collection)."""
    y, m = (int(x) for x in month.split("-"))
    nxt = f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"
    return f"{month}-01T00:00:00Z", f"{nxt}-01T00:00:00Z"


def manifest_path(raw: RawStore, name: str) -> Path:
    return raw.root / CENSUS_DIR / f"{name}.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ sampling frame


@dataclass(frozen=True)
class FrameMarket:
    market_id: str
    condition_id: str
    event_id: str | None
    category: str
    duration: str
    structure: str
    tokens: tuple[str, ...]

    @property
    def stratum(self) -> str:
        return f"{self.category}/{self.duration}"


def classify(market: dict[str, Any], event: dict[str, Any] | None) -> FrameMarket | None:
    """A listed market as a frame member, or None when it cannot be observed (no tokens, no
    open time). `event` is the full Gamma event (tags and market count)."""
    tokens = tuple(str(t) for t in json_list(market.get("clobTokenIds")))
    start = open_ts(market)
    end = parse_ts(market.get("endDate"))
    if not tokens or start is None or end is None:
        return None
    tags = [str(t.get("slug") or t.get("label") or "") for t in ((event or {}).get("tags") or [])]
    category = market_category(market.get("feeType"), tags) or "other"
    n_markets = len((event or {}).get("markets") or [])
    events = market.get("events") or []
    return FrameMarket(
        market_id=str(market["id"]),
        condition_id=str(market["conditionId"]),
        event_id=str(events[0]["id"]) if events and events[0].get("id") is not None else None,
        category=category,
        duration=duration_class(start, end),
        structure="sibling" if n_markets >= SIBLING_MIN_MARKETS else "binary",
        tokens=tokens,
    )


def allocate(
    frame: Sequence[FrameMarket], seed: int, per_stratum: int, ultra_short_cap: int
) -> list[FrameMarket]:
    """Uniform by seed within each (category, duration) stratum, capped."""
    by_stratum: dict[str, list[FrameMarket]] = defaultdict(list)
    seen: set[str] = set()
    for m in sorted(frame, key=lambda m: m.condition_id):
        if m.condition_id not in seen:
            seen.add(m.condition_id)
            by_stratum[m.stratum].append(m)
    chosen: list[FrameMarket] = []
    for stratum in sorted(by_stratum):
        cap = ultra_short_cap if stratum.endswith("/ultra_short") else per_stratum
        rng = random.Random(f"{seed}:{stratum}")
        members = by_stratum[stratum]
        chosen.extend(rng.sample(members, min(cap, len(members))))
    return sorted(chosen, key=lambda m: m.condition_id)


def _listing_markets(
    raw: RawStore, end_date_min: str, end_date_max: str
) -> dict[str, dict[str, Any]]:
    """Newest listed object per condition id for the closed listing of this window."""
    out: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.GAMMA_MARKETS_KEYSET):  # oldest first, newest wins
        u = rec.unit
        if (
            rec.status == 200
            and u.get("end_date_min") == end_date_min
            and u.get("end_date_max") == end_date_max
            and u.get("closed") is True
        ):
            for m in rec.json().get("markets") or []:
                out[str(m["conditionId"])] = m
    return out


def _latest_events(raw: RawStore) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.GAMMA_EVENT):
        if rec.status == 200 and rec.unit.get("id") is not None:
            out[str(rec.unit["id"])] = rec.json()
    return out


def _event_id(market: dict[str, Any]) -> str | None:
    evs = market.get("events") or []
    return str(evs[0]["id"]) if evs and evs[0].get("id") is not None else None


async def sample_census(
    settings: IngestSettings,
    *,
    month: str,
    seed: int,
    per_stratum: int = PER_STRATUM,
    ultra_short_cap: int = ULTRA_SHORT_CAP,
    concurrency: int | None = None,
    client: ApiClient | None = None,
) -> dict[str, Any]:
    """List the month, fetch its events (tags, sibling counts), allocate, write the manifest."""
    raw = RawStore(settings.raw_dir)
    name = census_name(month)
    path = manifest_path(raw, name)
    if path.exists():
        raise FileExistsError(f"census manifest {path} already exists; manifests are immutable")
    end_date_min, end_date_max = month_window(month)
    own_client = client is None
    api = client or ApiClient(raw, requests_per_second=settings.requests_per_second)
    try:
        saved, cursor, page, complete, prev = saved_listing(raw, end_date_min, end_date_max, True)
        if saved:
            log.info("resuming listing from %d saved pages (%d markets)", page, len(saved))
        if not complete:
            async for _ in pm.list_markets(
                api,
                end_date_min=end_date_min,
                end_date_max=end_date_max,
                closed=True,
                start_cursor=cursor,
                start_page=page,
                previous_cursor=prev,
            ):
                pass  # pages land in the raw store; read back below
        listed = _listing_markets(raw, end_date_min, end_date_max)
        log.info("listed %d closed markets ending in %s", len(listed), month)

        event_ids = sorted({e for m in listed.values() if (e := _event_id(m)) is not None})
        have = _latest_events(raw)
        checkpoints = Checkpoints(settings.checkpoint_dir, f"{name}-events")
        report = FetchReport()

        async def event_job(eid: str) -> dict[str, Any]:
            await pm.fetch_event(api, eid)
            return {}

        await _run_units(
            [(f"event:{e}", partial(event_job, e)) for e in event_ids if e not in have],
            checkpoints,
            report,
            concurrency or settings.concurrency,
            "events",
        )
        events = _latest_events(raw)
    finally:
        if own_client:
            await api.aclose()

    frame: list[FrameMarket] = []
    skipped = 0
    for m in listed.values():
        eid = _event_id(m)
        fm = classify(m, events.get(eid) if eid else None)
        if fm is None:
            skipped += 1
            continue
        frame.append(fm)
    chosen = allocate(frame, seed, per_stratum, ultra_short_cap)
    strata: dict[str, dict[str, int]] = {}
    for fm in frame:
        strata.setdefault(fm.stratum, {"available": 0, "selected": 0})["available"] += 1
    for fm in chosen:
        strata[fm.stratum]["selected"] += 1
    manifest = {
        "name": name,
        "month": month,
        "created_at": _now_iso(),
        "definition": {
            "method": "census: uniform by seed within (category, duration class)",
            "universe": "Gamma markets with end_date in [end_date_min, end_date_max], closed only",
            "end_date_min": end_date_min,
            "end_date_max": end_date_max,
            "strata_by": "market_category(feeType, event tags) x duration_class(end - open)",
            "per_stratum": per_stratum,
            "ultra_short_cap": ultra_short_cap,
            "seed": seed,
            "excluded_fields": "no outcome-dependent field (volume, liquidity, resolution) was used",
            "offsets_end": [label for label, _ in OFFSETS_END],
            "offsets_open": [label for label, _ in OFFSETS_OPEN],
            "price_window_days": PRICE_WINDOW_S // 86_400,
            "fine_window_hours": FINE_WINDOW_S // 3_600,
            "print_depth_days": PRINT_DEPTH_S // 86_400,
            "print_pages_max": PRINT_PAGES_MAX,
        },
        "frame": {
            "n_listed": len(listed),
            "n_frame": len(frame),
            "n_skipped_unobservable": skipped,
            "n_events": len(event_ids),
            "strata": dict(sorted(strata.items())),
        },
        "markets": [
            {
                "id": fm.market_id,
                "condition_id": fm.condition_id,
                "event_id": fm.event_id,
                "stratum": fm.stratum,
                "structure": fm.structure,
                "tokens": list(fm.tokens),
            }
            for fm in chosen
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    log.info("census %s: %d markets from a frame of %d", name, len(chosen), len(frame))
    return manifest


def load_manifest(raw: RawStore, month: str) -> dict[str, Any]:
    path = manifest_path(raw, census_name(month))
    if not path.exists():
        raise FileNotFoundError(f"no census manifest for {month}; run `census-sample` first")
    manifest: dict[str, Any] = json.loads(path.read_text())
    return manifest


# ------------------------------------------------------------------ fetch


async def fetch_census(
    settings: IngestSettings,
    month: str,
    *,
    concurrency: int | None = None,
    client: ApiClient | None = None,
) -> FetchReport:
    raw = RawStore(settings.raw_dir)
    manifest = load_manifest(raw, month)
    end_date_min, end_date_max = month_window(month)
    listed = _listing_markets(raw, end_date_min, end_date_max)
    name = manifest["name"]
    checkpoints = Checkpoints(settings.checkpoint_dir, name)
    report = FetchReport()
    workers = concurrency or settings.concurrency
    own_client = client is None
    api = client or ApiClient(raw, requests_per_second=settings.requests_per_second)
    try:
        members = manifest["markets"]
        conditions = sorted(m["condition_id"] for m in members)

        async def resolution_job(batch: list[str]) -> dict[str, Any]:
            rows = await pm.fetch_resolutions(api, batch)
            return {"rows": len(rows)}

        await _run_units(
            [
                (f"resolutions:{b[0]}:{len(b)}", partial(resolution_job, b))
                for b in pm.resolution_batches(conditions)
            ],
            checkpoints,
            report,
            workers,
            "resolutions",
        )

        by_condition = {m["condition_id"]: m for m in members}
        price_units: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = []
        trade_units: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = []
        for cid in conditions:
            member = by_condition[cid]
            g = listed.get(cid)
            if g is None:
                report.notes.append(f"{cid}: not in the saved listing; skipped")
                continue
            start = open_ts(g)
            end = parse_ts(g.get("endDate"))
            if start is None or end is None:
                report.notes.append(f"{cid}: no open/end timestamp; skipped")
                continue
            end_s = int(end.timestamp())
            start_s = int(start.timestamp())

            async def trades_job(c: str = cid, e: int = end_s) -> dict[str, Any]:
                info = await pm.walk_trades_window(
                    api, c, stop_before_ts=e - PRINT_DEPTH_S, max_pages=PRINT_PAGES_MAX
                )
                report.prints += int(info["prints"])
                return info

            trade_units.append((f"trades:{cid}", trades_job))
            windows = [(60, max(start_s, end_s - PRICE_WINDOW_S), end_s)]
            if member["stratum"].split("/")[1] in FINE_CLASSES:
                windows.append((1, max(start_s, end_s - FINE_WINDOW_S), end_s))
            for token in member["tokens"]:
                for fidelity, ws, we in windows:
                    if we <= ws:
                        continue

                    async def price_job(
                        t: str = str(token), a: int = ws, b: int = we, f: int = fidelity
                    ) -> dict[str, Any]:
                        points = await pm.fetch_price_history(api, t, a, b, f)
                        report.price_points += points
                        return {"points": points}

                    price_units.append((f"prices:{token}:{fidelity}:{ws}:{we}", price_job))
        await _run_units(trade_units, checkpoints, report, workers, "trades")
        await _run_units(price_units, checkpoints, report, workers, "prices")
    finally:
        if own_client:
            await api.aclose()
        log.info(
            "requests=%d retries=%d failures=%d",
            api.stats.requests,
            api.stats.retries,
            api.stats.failures,
        )
    return report


# ------------------------------------------------------------------ observations (pure)


@dataclass(frozen=True)
class Print:
    ts: int
    price: float
    size: float
    side: int  # +1 taker BUY, -1 taker SELL
    token: str


@dataclass(frozen=True)
class PrintWalk:
    """A market's prints as the census saw them: every distinct print from the saved pages,
    oldest first; whether the walk reached the market's first print; and whether the page cap
    cut it before the 8-day depth the offsets need."""

    prints: list[Print]
    complete: bool
    truncated: bool

    @property
    def earliest(self) -> int | None:
        return self.prints[0].ts if self.prints else None

    def covers(self, ts: int) -> bool:
        """Whether prints at or after `ts` were all fetched."""
        return self.complete or (self.earliest is not None and self.earliest <= ts)


def offset_times(open_s: int, end_s: int, close_s: int | None) -> list[tuple[str, int]]:
    """(label, ts) for every offset inside the market's life: end-relative ones must not
    precede the open, open-relative ones must not follow the close (closedTime, else end)."""
    close = end_s if close_s is None else min(close_s, end_s)
    out = []
    for label, off in OFFSETS_END:
        ts = end_s - off
        if ts >= open_s:
            out.append((label, ts))
    for label, off in OFFSETS_OPEN:
        ts = open_s + off
        if ts <= close:
            out.append((label, ts))
    return out


def mid_at(series: Sequence[tuple[int, float]], ts: int) -> float | None:
    """The CLOB sample at or before `ts` (series sorted by time)."""
    times = [t for t, _ in series]
    i = bisect_right(times, ts)
    return None if i == 0 else series[i - 1][1]


def staleness_minutes(print_times: Sequence[int], ts: int) -> int | None:
    i = bisect_right(print_times, ts)
    return None if i == 0 else (ts - print_times[i - 1]) // 60


def fill_in_window(
    prints: Sequence[Print], ts: int, window_s: int, limit: float
) -> tuple[float | None, float]:
    """(VWAP, shares) of taker-BUY prints in [ts, ts + window) at or below `limit`; None when
    nothing printed there — a fill that could not have happened is never invented."""
    times = [p.ts for p in prints]
    lo, hi = bisect_left(times, ts), bisect_left(times, ts + window_s)
    notional = shares = 0.0
    for p in prints[lo:hi]:
        if p.side == 1 and p.price <= limit + 1e-12:
            notional += p.price * p.size
            shares += p.size
    return (notional / shares if shares > 0 else None), shares


def liquidity_24h(prints: Sequence[Print], ts: int, walk: PrintWalk) -> float | None:
    """Same-side (taker-BUY) print notional in [ts − 24h, ts]; None unless the walk covered the
    whole window — a partial sum would understate liquidity and never says so."""
    start = ts - LIQ_WINDOW_S
    if not walk.covers(start):
        return None
    times = [p.ts for p in prints]
    lo, hi = bisect_left(times, start), bisect_right(times, ts)
    return sum(p.price * p.size for p in prints[lo:hi] if p.side == 1)


def payout_for(resolution: str, outcome_index: int) -> Decimal | None:
    if resolution == "INVALID":
        return Decimal("0.5")
    if resolution == "YES":
        return Decimal(1) if outcome_index == 0 else Decimal(0)
    if resolution == "NO":
        return Decimal(1) if outcome_index == 1 else Decimal(0)
    if resolution.startswith("OUTCOME_"):
        winner = int(resolution.removeprefix("OUTCOME_"))
        return Decimal(1) if outcome_index == winner else Decimal(0)
    return None


def current_fee_terms(
    category: str | None, schedules: Sequence[FeeScheduleEntry]
) -> FeeTerms | None:
    """The fee a fill in this category would pay today (v3 §3.5 CURRENT): the latest schedule
    whose fee type maps to the category. None when the category has no schedule (fee-free)."""
    best: FeeScheduleEntry | None = None
    for s in schedules:
        matches = category is not None and fee_category(s.fee_type) == category
        if matches and (best is None or s.effective_from > best.effective_from):
            best = s
    return None if best is None else FeeTerms(best.rate, best.exponent, f"{best.fee_type}@current")


@dataclass(frozen=True)
class MarketInputs:
    condition_id: str
    event_id: str | None
    tokens: tuple[str, ...]
    category: str | None
    duration: str
    structure: str
    deadline: str
    neg_risk: bool
    open_s: int
    end_s: int
    close_s: int | None
    terms: MarketTerms  # tick and the HISTORICAL fee fields
    resolution: str
    walk: PrintWalk
    series: dict[str, list[tuple[int, float]]]  # token → sorted (t, p)


def observations(
    m: MarketInputs, schedules: tuple[FeeScheduleEntry, ...], census_id: str
) -> list[tuple[Any, ...]]:
    """Rows of `census_observations` for one market, in (token, offset) order."""
    historical = FeeConfig(mode=FeeMode.HISTORICAL)
    current = current_fee_terms(m.category, schedules)
    tick = float(m.terms.tick_size)
    rows: list[tuple[Any, ...]] = []
    for idx, token in enumerate(m.tokens):
        series = m.series.get(token, [])
        token_prints = [p for p in m.walk.prints if p.token == token]
        print_times = [p.ts for p in token_prints]
        for label, ts in offset_times(m.open_s, m.end_s, m.close_s):
            mid = mid_at(series, ts)
            fill_10: tuple[float | None, float] = (None, 0.0)
            fill_30: tuple[float | None, float] = (None, 0.0)
            if mid is not None and m.walk.covers(ts):
                fill_10 = fill_in_window(token_prints, ts, FILL_WINDOWS[0][1], mid + tick)
                fill_30 = fill_in_window(token_prints, ts, FILL_WINDOWS[1][1], mid + tick)
            fee_hist = fee_cur = None
            if fill_10[0] is not None:
                price = Decimal(f"{fill_10[0]:.6f}")
                fee_hist = fee_amount(
                    Decimal(1), price, resolve_fee_terms(m.terms, ts, historical, schedules)
                )
                fee_cur = Decimal(0) if current is None else fee_amount(Decimal(1), price, current)
            liq = liquidity_24h(token_prints, ts, m.walk)
            stale = staleness_minutes(print_times, ts) if m.walk.covers(ts - 1) else None
            rows.append(
                (
                    census_id,
                    m.condition_id,
                    token,
                    m.event_id,
                    idx,
                    label,
                    _ts(datetime.fromtimestamp(ts, UTC)),
                    None if mid is None else f"{mid:.6f}",
                    stale,
                    None if fill_10[0] is None else f"{fill_10[0]:.6f}",
                    None if fill_30[0] is None else f"{fill_30[0]:.6f}",
                    f"{fill_10[1]:.6f}",
                    None if liq is None else f"{liq:.2f}",
                    None if fee_hist is None else f"{fee_hist:.6f}",
                    None if fee_cur is None else f"{fee_cur:.6f}",
                    _dec(payout_for(m.resolution, idx)),
                )
            )
    return rows


# ------------------------------------------------------------------ build


def print_walk(records: list[RawRecord], end_s: int) -> PrintWalk:
    """Prints from every saved trades page for a market. The newest walk (the last page-0 record
    onward) decides completeness; a walk stopped by the page cap before reaching `end − 8 d` is
    truncated."""
    ordered = sorted(records, key=lambda r: (r.fetched_at, int(r.unit["page"])))
    seen: dict[str, Print] = {}
    has_more = False
    for rec in ordered:
        body = rec.json()
        for t in body.get("data") or []:
            price, size = t.get("price"), t.get("size")
            side = str(t.get("side", "")).upper()
            if price is None or size is None or side not in ("BUY", "SELL"):
                continue
            seen[print_key(t)] = Print(
                int(t["timestamp"]),
                float(price),
                float(size),
                1 if side == "BUY" else -1,
                str(t["token_id"]),
            )
        pagination = body.get("pagination") or {}
        has_more = bool(pagination.get("has_more")) and bool(pagination.get("next_cursor"))
    prints = sorted(seen.values(), key=lambda p: (p.ts, p.token, p.price, p.size))
    complete = bool(ordered) and not has_more
    earliest = prints[0].ts if prints else None
    truncated = has_more and (earliest is None or earliest >= end_s - PRINT_DEPTH_S)
    return PrintWalk(prints, complete, truncated)


def _series_for(records: list[RawRecord]) -> list[tuple[int, float]]:
    samples: dict[int, float] = {}
    for rec in sorted(records, key=lambda r: r.sort_key):  # newer fetches win
        for point in rec.json().get("history") or []:
            p = point.get("p")
            if p is not None and 0 <= float(p) <= 1:
                samples[int(point["t"])] = float(p)
    return sorted(samples.items())


def fee_entries() -> tuple[FeeScheduleEntry, ...]:
    out = []
    for (
        venue,
        fee_type,
        rate,
        exponent,
        applies_to,
        eff_from,
        eff_to,
        _url,
        _notes,
    ) in fee_schedule_rows():
        if venue != "international":
            continue
        start = parse_ts(eff_from)
        stop = parse_ts(eff_to)
        assert start is not None
        out.append(
            FeeScheduleEntry(
                fee_type=fee_type,
                rate=Decimal(rate),
                exponent=int(exponent),
                applies_to=applies_to,
                effective_from=int(start.timestamp()),
                effective_to=None if stop is None else int(stop.timestamp()),
            )
        )
    return tuple(out)


def content_hash(conn: psycopg.Connection[Any], census_id: str) -> str:
    h = hashlib.sha256()
    with conn.cursor() as cur:
        for table, order in (
            ("census_markets", "condition_id"),
            ("census_observations", "clob_token_id, offset_label"),
        ):
            h.update(f"\n#table {table}\n".encode())
            query = sql.SQL(
                "COPY (SELECT * FROM {} WHERE census_id = {} ORDER BY {}) TO STDOUT"
            ).format(sql.Identifier(table), sql.Literal(census_id), sql.SQL(order))
            with cur.copy(query) as cp:
                for chunk in cp:
                    h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class CensusBuild:
    census_id: str
    content_hash: str
    n_markets: int
    n_observations: int
    quality: dict[str, Any]


def _epoch(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp())


def market_inputs(
    member: dict[str, Any],
    g: dict[str, Any],
    event: dict[str, Any] | None,
    v2_row: dict[str, Any] | None,
    trade_records: list[RawRecord],
    price_records: dict[str, list[RawRecord]],
) -> tuple[MarketInputs | None, str | None, str | None]:
    """(inputs, drop reason, resolution anomaly) for one sampled market."""
    start = open_ts(g)
    end = parse_ts(g.get("endDate"))
    if start is None or end is None or g.get("orderPriceMinTickSize") is None:
        return None, "NO_TIMES_OR_TICK", None
    tokens = tuple(member["tokens"])
    resolution = resolve_market(g, v2_row)
    tags = [str(t.get("slug") or t.get("label") or "") for t in ((event or {}).get("tags") or [])]
    category = market_category(g.get("feeType"), tags)
    schedule = g.get("feeSchedule") if g.get("feesEnabled") else None
    terms = MarketTerms(
        condition_id=member["condition_id"],
        event_id=member.get("event_id"),
        category=category,
        tick_size=Decimal(str(g["orderPriceMinTickSize"])),
        min_order_size=None if g.get("orderMinSize") is None else Decimal(str(g["orderMinSize"])),
        created_ts=_epoch(parse_ts(g.get("createdAt"))),
        open_ts=int(start.timestamp()),
        end_ts=int(end.timestamp()),
        fees_enabled=g.get("feesEnabled"),
        fee_type=g.get("feeType") if schedule else None,
        fee_rate=None
        if not schedule or schedule.get("rate") is None
        else Decimal(str(schedule["rate"])),
        fee_exponent=None if not schedule else schedule.get("exponent"),
        neg_risk=bool(g.get("negRisk")),
    )
    inputs = MarketInputs(
        condition_id=member["condition_id"],
        event_id=member.get("event_id"),
        tokens=tokens,
        category=category,
        duration=member["stratum"].split("/")[1],
        structure=member["structure"],
        deadline=deadline_type(g.get("question")),
        neg_risk=bool(g.get("negRisk")),
        open_s=int(start.timestamp()),
        end_s=int(end.timestamp()),
        close_s=_epoch(parse_ts(g.get("closedTime"))),
        terms=terms,
        resolution=resolution.resolution,
        walk=print_walk(trade_records, int(end.timestamp())),
        series={t: _series_for(price_records.get(t, [])) for t in tokens},
    )
    return inputs, None, resolution.anomaly


def build_census(settings: IngestSettings, month: str) -> CensusBuild:
    raw = RawStore(settings.raw_dir)
    manifest = load_manifest(raw, month)
    census_id = manifest["name"]
    end_date_min, end_date_max = month_window(month)
    listed = _listing_markets(raw, end_date_min, end_date_max)
    events = _latest_events(raw)
    schedules = fee_entries()

    v2_rows: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.DATA_V2_RESOLUTIONS):
        if rec.status == 200:
            for row in rec.json().get("data") or []:
                if row.get("condition_id"):
                    v2_rows[row["condition_id"]] = row
    wanted = {m["condition_id"] for m in manifest["markets"]}
    trades_by_condition: dict[str, list[RawRecord]] = defaultdict(list)
    for rec in raw.records(pm.DATA_V2_TRADES):
        if (
            rec.status == 200
            and rec.unit.get("kind") == "trades"
            and rec.unit.get("condition") in wanted
        ):
            trades_by_condition[str(rec.unit["condition"])].append(rec)
    tokens_wanted = {t for m in manifest["markets"] for t in m["tokens"]}
    prices_by_token: dict[str, list[RawRecord]] = defaultdict(list)
    for rec in raw.records(pm.CLOB_PRICES_HISTORY):
        if rec.status == 200 and str(rec.unit.get("token")) in tokens_wanted:
            prices_by_token[str(rec.unit["token"])].append(rec)

    quality: dict[str, Any] = {
        "markets_in": len(manifest["markets"]),
        "markets_dropped": [],
        "resolution_anomalies": 0,
        "prints_truncated": 0,
        "prints_complete": 0,
        "unresolved": 0,
        "observations_with_mid": 0,
        "observations_with_fill_10m": 0,
    }
    market_rows: list[tuple[Any, ...]] = []
    obs_rows: list[tuple[Any, ...]] = []
    for member in sorted(manifest["markets"], key=lambda m: m["condition_id"]):
        cid = member["condition_id"]
        g = listed.get(cid)
        if g is None:
            quality["markets_dropped"].append({"condition_id": cid, "reason": "NO_LISTING"})
            continue
        eid = member.get("event_id")
        inputs, reason, anomaly = market_inputs(
            member,
            g,
            events.get(eid) if eid else None,
            v2_rows.get(cid),
            trades_by_condition.get(cid, []),
            prices_by_token,
        )
        if inputs is None:
            quality["markets_dropped"].append({"condition_id": cid, "reason": reason})
            continue
        if anomaly:
            quality["resolution_anomalies"] += 1
        if inputs.resolution == "UNRESOLVED":
            quality["unresolved"] += 1
        quality["prints_truncated"] += int(inputs.walk.truncated)
        quality["prints_complete"] += int(inputs.walk.complete)
        market_rows.append(
            (
                census_id,
                cid,
                eid,
                inputs.category,
                inputs.duration,
                inputs.structure,
                inputs.deadline,
                len(inputs.tokens),
                inputs.walk.truncated,
                inputs.resolution,
                inputs.neg_risk,
                _ts(datetime.fromtimestamp(inputs.open_s, UTC)),
                _ts(datetime.fromtimestamp(inputs.end_s, UTC)),
                _dec(inputs.terms.tick_size),
            )
        )
        rows = observations(inputs, schedules, census_id)
        quality["observations_with_mid"] += sum(1 for r in rows if r[7] is not None)
        quality["observations_with_fill_10m"] += sum(1 for r in rows if r[9] is not None)
        obs_rows.extend(rows)
    quality["markets_dropped_n"] = len(quality["markets_dropped"])
    quality["markets_dropped"] = quality["markets_dropped"][:200]

    with psycopg.connect(psycopg_url(settings.database_url)) as conn:
        _session(conn)
        with conn.transaction(), conn.cursor() as cur:
            for table in ("census_observations", "census_markets"):
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE census_id = %s").format(sql.Identifier(table)),
                    (census_id,),
                )
            with cur.copy(
                "COPY census_markets (census_id, condition_id, event_id, category, duration_class, "
                "structure, deadline_type, n_outcomes, prints_truncated, resolution, neg_risk, "
                "open_ts, end_ts, tick_size) FROM STDIN"
            ) as cp:
                for row in market_rows:
                    cp.write_row(row)
            with cur.copy(
                "COPY census_observations (census_id, condition_id, clob_token_id, event_id, "
                "outcome_index, offset_label, ts, mid, staleness_minutes, fill_10m, fill_30m, "
                "fill_size_10m, liq_24h, fee_entry, fee_entry_current, payout) FROM STDIN"
            ) as cp:
                for row in obs_rows:
                    cp.write_row(row)
            digest = content_hash(conn, census_id)
            cur.execute(
                """
                INSERT INTO census_runs (census_id, built_at, content_hash, n_markets,
                                         n_observations, manifest, quality)
                VALUES (%s, now(), %s, %s, %s, %s, %s)
                ON CONFLICT (census_id) DO UPDATE SET built_at = excluded.built_at,
                  content_hash = excluded.content_hash, n_markets = excluded.n_markets,
                  n_observations = excluded.n_observations, manifest = excluded.manifest,
                  quality = excluded.quality
                """,
                (
                    census_id,
                    digest,
                    len(market_rows),
                    len(obs_rows),
                    json.dumps(
                        {k: v for k, v in manifest.items() if k != "markets"}, sort_keys=True
                    ),
                    json.dumps(quality, sort_keys=True),
                ),
            )
    return CensusBuild(census_id, digest, len(market_rows), len(obs_rows), quality)


def verify_census(settings: IngestSettings, month: str) -> tuple[str, str | None]:
    census_id = census_name(month)
    with psycopg.connect(psycopg_url(settings.database_url)) as conn:
        _session(conn)
        row = conn.execute(
            "SELECT content_hash FROM census_runs WHERE census_id = %s", (census_id,)
        ).fetchone()
        return content_hash(conn, census_id), (row[0] if row else None)
