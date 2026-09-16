"""Rebuild the normalized layer from the raw layer alone (PRD §4.5, `make rebuild-normalized`).

Determinism rules, so two rebuilds from the same raw data are bit-identical:
- Raw records are read in (fetched_at, filename) order; later fetches win.
- Rows are loaded in a fixed order, so bigserial ids are reproducible (identities restart).
- No wall-clock value is written to a normalized row: `ingested_at` is the raw fetch time.
- The content hash is computed from what Postgres returns (`COPY … ORDER BY` primary key), not
  from what we meant to write.

`dataset_versions` rows record each build and are not part of the hashed content.

Census months (PRD v3 D1, `raw/_census/*.json`) join the layer as collections named
`census-<month>`: their markets come from the saved listing pages, their bars are the census's
hourly samples (`source = prices_history_60`, bar timestamps on the hour) and their prints the
census walks (the last ~8 days of each market). They are survivorship-free and let candidate
strategies run through the engine at `bar_interval_minutes = 60` without a second fetch.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import shutil
import traceback
import uuid
from array import array
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

import psycopg

from syntha_ingestion import polymarket as pm
from syntha_ingestion.config import IngestSettings, psycopg_url
from syntha_ingestion.parse import (
    TAG_CATEGORIES,
    deadline_type,
    json_list,
    market_category,
    open_ts,
    parse_ts,
    print_key,
    resolve_market,
)
from syntha_ingestion.raw_store import RawRecord, RawStore

log = logging.getLogger(__name__)

COLLECTION_NAMESPACE = uuid.UUID("5f0c4a1e-4b8e-4a52-9d0a-7a9c3f5e2b11")

# (table, ORDER BY) in load order. The hash covers exactly these tables.
NORMALIZED_TABLES: tuple[tuple[str, str], ...] = (
    ("fee_schedules", "id"),
    ("collections", "id"),
    ("collection_markets", "collection_id, condition_id"),
    ("events", "id"),
    ("markets", "condition_id"),
    ("outcomes", "clob_token_id"),
    ("trade_prints", "id"),
    ("price_bars", "clob_token_id, ts"),
)

__all__ = ["TAG_CATEGORIES"]  # re-exported for callers that used it from here

_COPY_CHUNK = 1 << 20


@dataclass
class BuildLog:
    """Counters and anomalies; becomes part of the data-quality report."""

    rows_in: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    rows_written: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    rows_rejected: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    markets_dropped: list[dict[str, str]] = field(default_factory=list)
    resolution_anomalies: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_in": dict(sorted(self.rows_in.items())),
            "rows_written": dict(sorted(self.rows_written.items())),
            "rows_rejected": dict(sorted(self.rows_rejected.items())),
            "markets_dropped": self.markets_dropped,
            "resolution_anomalies": self.resolution_anomalies,
        }


@dataclass(frozen=True)
class RebuildResult:
    dataset_version_id: uuid.UUID
    content_hash: str
    counts: dict[str, int]
    quality: dict[str, Any]


# ------------------------------------------------------------------ formatting helpers


def _ts(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f+00")


def _epoch_ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M:%S+00")


def _dec(value: Any, places: int = 6) -> str | None:
    if value is None or value == "":
        return None
    return f"{Decimal(str(value)):.{places}f}"


def _latest(records: Iterable[RawRecord], key: str) -> dict[str, RawRecord]:
    out: dict[str, RawRecord] = {}
    for rec in records:  # already sorted oldest → newest
        if rec.status == 200 and rec.unit.get(key) is not None:
            out[str(rec.unit[key])] = rec
    return out


# ------------------------------------------------------------------ table builders


def fee_schedule_rows() -> list[tuple[Any, ...]]:
    text = resources.files("syntha_ingestion.seeds").joinpath("fee_schedules.json").read_text()
    seed = json.loads(text)
    rows = []
    for s in sorted(seed["schedules"], key=lambda s: (s["fee_type"], s["effective_from"])):
        rows.append(
            (
                seed["venue"],
                s["fee_type"],
                _dec(s["rate"]),
                s["exponent"],
                s["applies_to"],
                _ts(parse_ts(s["effective_from"])),
                _ts(parse_ts(s.get("effective_to"))),
                s["source_url"],
                s.get("notes"),
            )
        )
    return rows


CENSUS_DIR = "_census"


def census_collection_rows(
    raw: RawStore,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], dict[str, str], list[tuple[str, str]]]:
    """collections, collection_markets, market_id → condition_id and the listing windows for
    every census manifest (v3 D1)."""
    collections, members, market_ids, windows = [], [], {}, []
    base = raw.root / CENSUS_DIR
    for path in sorted(base.glob("*.json")) if base.exists() else []:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        cid = uuid.uuid5(COLLECTION_NAMESPACE, manifest["name"])
        definition = {
            "kind": "census",
            "month": manifest.get("month"),
            "definition": manifest.get("definition"),
            "frame": manifest.get("frame"),
        }
        collections.append(
            (
                str(cid),
                manifest["name"],
                json.dumps(definition, sort_keys=True),
                _ts(parse_ts(manifest["created_at"])),
            )
        )
        d = manifest.get("definition") or {}
        windows.append((str(d.get("end_date_min")), str(d.get("end_date_max"))))
        for m in manifest["markets"]:
            members.append((str(cid), m["condition_id"]))
            market_ids[str(m["id"])] = m["condition_id"]
    collections.sort(key=lambda r: r[0])
    return collections, sorted(set(members)), market_ids, windows


def listing_market_objects(
    raw: RawStore, windows: list[tuple[str, str]], market_ids: set[str]
) -> dict[str, tuple[RawRecord, dict[str, Any]]]:
    """Newest listed Gamma object per wanted market id from the closed keyset listings of
    `windows` (the census keeps no per-id refetch: listing rows already carry `events`). Only
    `market_ids` are kept: a month's listing holds up to 700k markets, the sample 20k."""
    wanted = set(windows)
    out: dict[str, tuple[RawRecord, dict[str, Any]]] = {}
    for rec in raw.records(pm.GAMMA_MARKETS_KEYSET):  # oldest first, newest wins
        u = rec.unit
        if (
            rec.status == 200
            and u.get("closed") is True
            and (str(u.get("end_date_min")), str(u.get("end_date_max"))) in wanted
        ):
            for market in rec.json().get("markets") or []:
                mid = str(market["id"])
                if mid in market_ids:
                    out[mid] = (rec, market)
    return out


def collection_rows(
    raw: RawStore,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], dict[str, str]]:
    """collections, collection_markets, and market_id → condition_id for every member."""
    collections, members, market_ids = [], [], {}
    for manifest in raw.manifests():
        cid = uuid.uuid5(COLLECTION_NAMESPACE, manifest["name"])
        definition = {k: manifest[k] for k in ("definition", "strata", "listed") if k in manifest}
        collections.append(
            (
                str(cid),
                manifest["name"],
                json.dumps(definition, sort_keys=True),
                _ts(parse_ts(manifest["created_at"])),
            )
        )
        for m in manifest["markets"]:
            members.append((str(cid), m["condition_id"]))
            market_ids[str(m["id"])] = m["condition_id"]
    collections.sort(key=lambda r: r[0])
    members = sorted(set(members))
    return collections, members, market_ids


@dataclass
class MarketBuild:
    events: list[tuple[Any, ...]]
    markets: list[tuple[Any, ...]]
    outcomes: list[tuple[Any, ...]]
    tokens: dict[str, str]  # clob_token_id → condition_id


def market_rows(
    raw: RawStore,
    wanted: dict[str, str],
    build: BuildLog,
    objects: dict[str, tuple[RawRecord, dict[str, Any]]] | None = None,
) -> MarketBuild:
    """`objects` overrides where market objects come from (census months: listing pages)."""
    market_objects = pm.latest_market_objects(raw) if objects is None else objects
    event_recs = _latest(raw.records(pm.GAMMA_EVENT), "id")

    v2_rows: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.DATA_V2_RESOLUTIONS):
        if rec.status != 200:
            continue
        for row in rec.json().get("data") or []:
            if row.get("condition_id"):
                v2_rows[row["condition_id"]] = row

    events: dict[str, tuple[Any, ...]] = {}
    event_tags: dict[str, list[str]] = {}
    for eid, rec in event_recs.items():
        ev = rec.json()
        tags = sorted({t["slug"] for t in (ev.get("tags") or []) if t.get("slug")})
        event_tags[eid] = tags
        events[eid] = (
            eid,
            ev.get("slug") or "",
            ev.get("title") or "",
            None,
            tags,
            _ts(parse_ts(ev.get("createdAt"))),
            _ts(parse_ts(rec.fetched_at)),
        )

    markets, outcomes, tokens = [], [], {}
    for market_id, condition_id in sorted(wanted.items(), key=lambda kv: kv[1]):
        build.rows_in["markets"] += 1
        found = market_objects.get(market_id)
        if found is None:
            build.markets_dropped.append({"condition_id": condition_id, "reason": "NO_METADATA"})
            continue
        mrec, g = found
        if g.get("conditionId") != condition_id:
            build.markets_dropped.append(
                {"condition_id": condition_id, "reason": "CONDITION_ID_MISMATCH"}
            )
            continue
        token_ids = [str(t) for t in json_list(g.get("clobTokenIds"))]
        names = [str(o) for o in json_list(g.get("outcomes"))]
        if not token_ids or len(token_ids) != len(names):
            build.markets_dropped.append(
                {"condition_id": condition_id, "reason": "BAD_OUTCOME_TOKENS"}
            )
            continue
        if g.get("orderPriceMinTickSize") is None:
            build.markets_dropped.append({"condition_id": condition_id, "reason": "NO_TICK_SIZE"})
            continue

        ev_list = g.get("events") or []
        event_id = str(ev_list[0]["id"]) if ev_list else None
        if event_id and event_id not in events:
            ev = ev_list[0]
            events[event_id] = (
                event_id,
                ev.get("slug") or "",
                ev.get("title") or "",
                None,
                None,
                _ts(parse_ts(ev.get("createdAt"))),
                _ts(parse_ts(mrec.fetched_at)),
            )

        resolution = resolve_market(g, v2_rows.get(condition_id))
        if resolution.anomaly:
            build.resolution_anomalies.append(
                {"condition_id": condition_id, "detail": resolution.anomaly}
            )

        schedule = g.get("feeSchedule") if g.get("feesEnabled") else None
        markets.append(
            (
                condition_id,
                event_id,
                g.get("questionID"),
                g.get("slug") or "",
                g.get("question") or "",
                market_category(g.get("feeType"), event_tags.get(event_id or "", [])),
                bool(g.get("negRisk")),
                _dec(g["orderPriceMinTickSize"]),
                _dec(g.get("orderMinSize")),
                g.get("feesEnabled"),
                g.get("feeType") if schedule else None,
                _dec(schedule.get("rate")) if schedule else None,
                schedule.get("exponent") if schedule else None,
                _ts(parse_ts(g.get("createdAt"))),
                _ts(open_ts(g)),
                _ts(parse_ts(g.get("endDate"))),
                _ts(parse_ts(g.get("closedTime"))),
                _ts(resolution.resolved_at),
                resolution.resolution,
                _dec(g.get("volumeNum"), 2),
                _ts(parse_ts(mrec.fetched_at)),
                deadline_type(g.get("question")),
                None if g.get("negRiskOther") is None else bool(g.get("negRiskOther")),
            )
        )
        for i, (tok, name) in enumerate(zip(token_ids, names, strict=True)):
            outcomes.append((tok, condition_id, i, name))
            tokens[tok] = condition_id
        build.rows_written["markets"] += 1

    return MarketBuild(
        events=[events[k] for k in sorted(events)],
        markets=markets,
        outcomes=sorted(outcomes),
        tokens=tokens,
    )


def trade_lines(
    raw: RawStore, tokens: dict[str, str], build: BuildLog, spool: Path
) -> dict[str, array[int]]:
    """Write the COPY lines for trade_prints to `spool` (market by market, each market's prints
    de-duplicated and sorted by (ts, key)) and return, per token, its print epoch seconds in
    ascending order. Streaming keeps memory to one market's pages at a time: twelve census
    months hold ~60M prints, which as one dict of lines needed more RAM than the machine has."""
    conditions = set(tokens.values())
    by_condition: dict[str, list[RawRecord]] = defaultdict(list)
    for rec in raw.records(pm.DATA_V2_TRADES):  # oldest first (dedup: last fetch wins)
        cid = rec.unit.get("condition")
        if rec.status == 200 and cid in conditions:
            by_condition[str(cid)].append(rec)
    accepted = written = 0
    by_token: dict[str, array[int]] = defaultdict(lambda: array("q"))
    with spool.open("w", encoding="utf-8") as out:
        for cid in sorted(by_condition):
            rows: dict[str, tuple[int, str, str]] = {}  # print_key → (ts, token, line)
            for rec in by_condition[cid]:
                for t in rec.json().get("data") or []:
                    build.rows_in["trade_prints"] += 1
                    tok = str(t.get("token_id"))
                    price, size = t.get("price"), t.get("size")
                    side = str(t.get("side", "")).upper()
                    if tokens.get(tok) != t.get("condition_id"):
                        build.rows_rejected["trade_prints:unknown_token"] += 1
                        continue
                    if (
                        price is None
                        or not 0 < float(price) <= 1
                        or size is None
                        or float(size) <= 0
                        or side not in ("BUY", "SELL")
                    ):
                        build.rows_rejected["trade_prints:invalid_values"] += 1
                        continue
                    accepted += 1
                    key = print_key(t)
                    ts = int(t["timestamp"])
                    wallet = str(t.get("proxy_wallet") or "").strip() or "\\N"  # v3 E4
                    line = (
                        f"{_epoch_ts(ts)}\t{key}\t{t['condition_id']}\t{tok}\t"
                        f"{float(price):.6f}\t{float(size):.6f}\t{side}\t{wallet}\n"
                    )
                    rows[key] = (ts, tok, line)
            for _, (ts, tok, line) in sorted(rows.items(), key=lambda kv: (kv[1][0], kv[0])):
                out.write(line)
                by_token[tok].append(ts)
                written += 1
    build.rows_written["trade_prints"] = written
    build.rows_rejected["trade_prints:duplicate"] = accepted - written
    return by_token


def _spooled(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as f:
        yield from f


def bar_lines(
    raw: RawStore,
    tokens: dict[str, str],
    prints: Mapping[str, Sequence[int]],
    build: BuildLog,
    fidelity: int = 1,
) -> Iterator[str]:
    """COPY lines for price_bars, one token at a time (keeps memory flat). `fidelity` selects
    the sample grain (1 for collections, 60 for census months); a bar's timestamp is floored to
    that grain and its staleness is measured at the bar's end."""
    by_token: dict[str, list[RawRecord]] = defaultdict(list)
    for rec in raw.records(pm.CLOB_PRICES_HISTORY):
        tok = str(rec.unit.get("token"))
        if rec.status == 200 and tok in tokens and int(rec.unit.get("fidelity", 1)) == fidelity:
            by_token[tok].append(rec)
    grain = fidelity * 60
    source = "prices_history" if fidelity == 1 else f"prices_history_{fidelity}"

    for tok in sorted(by_token):
        # minute → (record order, observed t, price). A newer fetch always wins; within one
        # fetch, the latest observation in the minute wins.
        samples: dict[int, tuple[int, int, float]] = {}
        for order, rec in enumerate(by_token[tok]):  # records are oldest fetch first
            for point in rec.json().get("history") or []:
                build.rows_in["price_bars"] += 1
                t, p = int(point["t"]), point.get("p")
                if p is None or not 0 <= float(p) <= 1:
                    build.rows_rejected["price_bars:invalid_price"] += 1
                    continue
                minute = t // grain * grain
                prev = samples.get(minute)
                if prev is not None:
                    # Overlapping windows or two samples in one minute: one bar survives.
                    build.rows_rejected["price_bars:merged_same_minute"] += 1
                if prev is None or (order, t) >= (prev[0], prev[1]):
                    samples[minute] = (order, t, float(p))
        token_prints = prints.get(tok, [])
        for minute in sorted(samples):
            j = bisect_left(token_prints, minute + grain)  # prints before the end of this bar
            last = token_prints[j - 1] if j else None
            in_bar = last is not None and last >= minute
            # minutes from the print's minute to the bar's last minute (0 inside a 1-minute bar)
            staleness = (
                r"\N" if last is None else str((minute + grain - 60 - last // 60 * 60) // 60)
            )
            build.rows_written["price_bars"] += 1
            yield (
                f"{tok}\t{_epoch_ts(minute)}\t{samples[minute][2]:.6f}\t"
                f"{'f' if in_bar else 't'}\t{staleness}\t{source}\n"
            )


# ------------------------------------------------------------------ database


def _copy_rows(
    cur: psycopg.Cursor[Any], table: str, columns: str, rows: Iterable[tuple[Any, ...]]
) -> int:
    n = 0
    with cur.copy(f"COPY {table} ({columns}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def _copy_lines(cur: psycopg.Cursor[Any], table: str, columns: str, lines: Iterable[str]) -> int:
    n = 0
    buf: list[str] = []
    size = 0
    with cur.copy(f"COPY {table} ({columns}) FROM STDIN") as cp:
        for line in lines:
            buf.append(line)
            size += len(line)
            n += 1
            if size >= _COPY_CHUNK:
                cp.write("".join(buf))
                buf, size = [], 0
        if buf:
            cp.write("".join(buf))
    return n


def content_hash(conn: psycopg.Connection[Any]) -> str:
    h = hashlib.sha256()
    with conn.cursor() as cur:
        for table, order in NORMALIZED_TABLES:
            h.update(f"\n#table {table}\n".encode())
            with cur.copy(f"COPY (SELECT * FROM {table} ORDER BY {order}) TO STDOUT") as cp:
                for chunk in cp:
                    h.update(chunk)
    return h.hexdigest()


def _session(conn: psycopg.Connection[Any]) -> None:
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET DateStyle TO ISO")
    conn.execute("SET extra_float_digits TO 0")


def require_free_space(path: Path, min_free_gb: float) -> float:
    """Free gigabytes on the filesystem holding `path`; raises when below `min_free_gb`.

    The database's files live in Docker's VM image on the same disk as `data/`, so the host's
    free space is what a rebuild can consume (docs/DECISIONS.md, "Disk incident")."""
    free_gb = shutil.disk_usage(path).free / 1e9
    if free_gb < min_free_gb:
        raise RuntimeError(
            f"{free_gb:.0f} GB free under {path}; a rebuild needs at least {min_free_gb:.0f} GB "
            "(the new layer is written before the old one is dropped). Free space or lower "
            "--min-free-gb deliberately."
        )
    return free_gb


def truncate_normalized(database_url: str) -> None:
    """Drop the current normalized layer in its own transaction, before the rebuild's, so the
    rebuild's peak disk use is the new layer alone (a rolled-back rebuild then leaves no layer,
    which `make verify-normalized` reports; the tables are disposable by design)."""
    with (
        psycopg.connect(psycopg_url(database_url)) as conn,
        conn.transaction(),
        conn.cursor() as cur,
    ):
        cur.execute("TRUNCATE " + ", ".join(t for t, _ in NORMALIZED_TABLES) + " RESTART IDENTITY")
    log.info("normalized tables truncated ahead of the rebuild")


def rebuild(
    settings: IngestSettings, *, truncate_first: bool = False, min_free_gb: float = 0.0
) -> RebuildResult:
    from syntha_ingestion.quality import build_quality

    if min_free_gb > 0:
        log.info("free space: %.0f GB", require_free_space(settings.data_dir, min_free_gb))
    if truncate_first:
        truncate_normalized(settings.database_url)
    raw = RawStore(settings.raw_dir)
    build = BuildLog()

    fees = fee_schedule_rows()
    collections, members, wanted = collection_rows(raw)
    c_collections, c_members, c_wanted, windows = census_collection_rows(raw)
    if not wanted and not c_wanted:
        raise RuntimeError(f"no collection manifests under {settings.raw_dir}; run `sample` first")
    mb = market_rows(raw, wanted, build)
    # census months (v3 D1): markets not already in a collection come from the listing pages
    have = set(wanted.values())
    census_only = {mid: cid for mid, cid in c_wanted.items() if cid not in have}
    mbc = (
        market_rows(
            raw,
            census_only,
            build,
            objects=listing_market_objects(raw, windows, set(census_only)),
        )
        if census_only
        else MarketBuild([], [], [], {})
    )
    known_events = {e[0] for e in mb.events}
    all_events = sorted(
        mb.events + [e for e in mbc.events if e[0] not in known_events], key=lambda e: e[0]
    )
    all_tokens = {**mb.tokens, **mbc.tokens}
    spool = settings.data_dir / "checkpoints" / "trade_prints.spool"
    spool.parent.mkdir(parents=True, exist_ok=True)
    prints = trade_lines(raw, all_tokens, build, spool)
    n_prints = build.rows_written["trade_prints"]
    log.info(
        "markets=%d (+%d census) tokens=%d prints=%d; loading",
        len(mb.markets),
        len(mbc.markets),
        len(all_tokens),
        n_prints,
    )

    with psycopg.connect(psycopg_url(settings.database_url)) as conn:
        _session(conn)
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "TRUNCATE " + ", ".join(t for t, _ in NORMALIZED_TABLES) + " RESTART IDENTITY"
            )
            _copy_rows(
                cur,
                "fee_schedules",
                "venue, fee_type, rate, exponent, applies_to, effective_from, effective_to, "
                "source_url, notes",
                fees,
            )
            _copy_rows(
                cur,
                "collections",
                "id, name, definition, created_at",
                sorted(collections + c_collections, key=lambda r: r[0]),
            )
            _copy_rows(
                cur,
                "collection_markets",
                "collection_id, condition_id",
                sorted(set(members + c_members)),
            )
            _copy_rows(
                cur,
                "events",
                "id, slug, title, category, tags, created_at, ingested_at",
                all_events,
            )
            _copy_rows(
                cur,
                "markets",
                "condition_id, event_id, question_id, slug, question, category, neg_risk, "
                "tick_size, min_order_size, fees_enabled, fee_type, fee_rate, fee_exponent, "
                "created_at, open_ts, end_date, closed_time, resolved_at, resolution, "
                "final_volume, ingested_at, deadline_type, neg_risk_other",
                sorted(mb.markets + mbc.markets, key=lambda r: r[0]),
            )
            _copy_rows(
                cur,
                "outcomes",
                "clob_token_id, condition_id, outcome_index, outcome_name",
                sorted(mb.outcomes + mbc.outcomes),
            )
            # Bulk load without the secondary indexes: maintaining them row by row made the
            # COPY of 68M prints take hours; rebuilding them afterwards takes minutes. The
            # primary key stays (Timescale needs it); the unique key is guaranteed by the
            # per-market de-duplication above and re-checked when the constraint returns.
            cur.execute("ALTER TABLE trade_prints DROP CONSTRAINT uq_trade_prints_print_key_ts")
            cur.execute("DROP INDEX ix_trade_prints_clob_token_id_ts")
            _copy_lines(
                cur,
                "trade_prints",
                "ts, print_key, condition_id, clob_token_id, price, size, side, proxy_wallet",
                _spooled(spool),
            )
            cur.execute(
                "ALTER TABLE trade_prints ADD CONSTRAINT uq_trade_prints_print_key_ts "
                "UNIQUE (print_key, ts)"
            )
            cur.execute(
                "CREATE INDEX ix_trade_prints_clob_token_id_ts ON trade_prints (clob_token_id, ts)"
            )
            n_bars = _copy_lines(
                cur,
                "price_bars",
                "clob_token_id, ts, price, is_stale, staleness_minutes, source",
                itertools.chain(
                    bar_lines(raw, mb.tokens, prints, build),
                    bar_lines(raw, mbc.tokens, prints, build, fidelity=60),
                ),
            )
            log.info("price_bars loaded: %d; hashing", n_bars)

            digest = content_hash(conn)
            try:
                # A savepoint, so a failed report statement does not abort the load's
                # transaction; no parallel workers, whose dynamic shared memory is what ran out
                # (Docker's /dev/shm) on the 76M-print self-join.
                with conn.transaction():
                    cur.execute("SET LOCAL max_parallel_workers_per_gather = 0")
                    quality = build_quality(conn, build.as_dict())
            except Exception:  # a report must not discard a multi-hour load
                log.exception("data-quality report failed; the build is kept without it")
                quality = {"build": build.as_dict(), "error": traceback.format_exc()[-4000:]}
            counts = {
                "markets": len(mb.markets) + len(mbc.markets),
                "tokens": len(all_tokens),
                "bars": n_bars,
                "prints": n_prints,
            }
            version_id = uuid.uuid4()
            cur.execute(
                """
                INSERT INTO dataset_versions
                  (id, built_at, content_hash, n_markets, n_tokens, n_bars, n_prints,
                   coverage_start, coverage_end, notes, quality)
                SELECT %s, now(), %s, %s, %s, %s, %s, min(ts), max(ts), %s, %s FROM price_bars
                """,
                (
                    version_id,
                    digest,
                    counts["markets"],
                    counts["tokens"],
                    counts["bars"],
                    counts["prints"],
                    "rebuilt from raw layer",
                    json.dumps(quality, sort_keys=True),
                ),
            )
    spool.unlink(missing_ok=True)
    return RebuildResult(version_id, digest, counts, quality)


def verify(settings: IngestSettings) -> tuple[str, str | None]:
    """(hash of the current normalized tables, content_hash of the latest dataset version)."""
    with psycopg.connect(psycopg_url(settings.database_url)) as conn:
        _session(conn)
        latest = conn.execute(
            "SELECT content_hash FROM dataset_versions ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
        return content_hash(conn), (latest[0] if latest else None)
