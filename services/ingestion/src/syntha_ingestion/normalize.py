"""Rebuild the normalized layer from the raw layer alone (PRD §4.5, `make rebuild-normalized`).

Determinism rules, so two rebuilds from the same raw data are bit-identical:
- Raw records are read in (fetched_at, filename) order; later fetches win.
- Rows are loaded in a fixed order, so bigserial ids are reproducible (identities restart).
- No wall-clock value is written to a normalized row: `ingested_at` is the raw fetch time.
- The content hash is computed from what Postgres returns (`COPY … ORDER BY` primary key), not
  from what we meant to write.

`dataset_versions` rows record each build and are not part of the hashed content.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from importlib import resources
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


def market_rows(raw: RawStore, wanted: dict[str, str], build: BuildLog) -> MarketBuild:
    market_objects = pm.latest_market_objects(raw)
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
    raw: RawStore, tokens: dict[str, str], build: BuildLog
) -> tuple[list[str], dict[str, list[int]]]:
    """COPY lines for trade_prints and, per token, sorted print epoch seconds."""
    conditions = set(tokens.values())
    accepted = 0
    rows: dict[str, tuple[int, str, str]] = {}  # print_key → (ts, token, line)
    for rec in raw.records(pm.DATA_V2_TRADES):
        if rec.status != 200 or rec.unit.get("condition") not in conditions:
            continue
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
            wallet = str(t.get("proxy_wallet") or "").strip() or "\\N"  # v3 E4; \N = NULL
            line = (
                f"{_epoch_ts(ts)}\t{key}\t{t['condition_id']}\t{tok}\t"
                f"{float(price):.6f}\t{float(size):.6f}\t{side}\t{wallet}\n"
            )
            rows[key] = (ts, tok, line)

    ordered = sorted(rows.items(), key=lambda kv: (kv[1][0], kv[0]))
    by_token: dict[str, list[int]] = defaultdict(list)
    for _, (ts, tok, _) in ordered:
        by_token[tok].append(ts)
    build.rows_written["trade_prints"] = len(ordered)
    build.rows_rejected["trade_prints:duplicate"] = accepted - len(ordered)
    return [line for _, (_, _, line) in ordered], by_token


def bar_lines(
    raw: RawStore, tokens: dict[str, str], prints: dict[str, list[int]], build: BuildLog
) -> Iterator[str]:
    """COPY lines for price_bars, one token at a time (keeps memory flat)."""
    by_token: dict[str, list[RawRecord]] = defaultdict(list)
    for rec in raw.records(pm.CLOB_PRICES_HISTORY):
        tok = str(rec.unit.get("token"))
        # census fetches (v3 D1) share the endpoint at fidelity 60; bars are 1-minute only
        if rec.status == 200 and tok in tokens and int(rec.unit.get("fidelity", 1)) == 1:
            by_token[tok].append(rec)

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
                minute = t // 60 * 60
                prev = samples.get(minute)
                if prev is not None:
                    # Overlapping windows or two samples in one minute: one bar survives.
                    build.rows_rejected["price_bars:merged_same_minute"] += 1
                if prev is None or (order, t) >= (prev[0], prev[1]):
                    samples[minute] = (order, t, float(p))
        token_prints = prints.get(tok, [])
        for minute in sorted(samples):
            j = bisect_left(token_prints, minute + 60)  # prints before the end of this bar
            last = token_prints[j - 1] if j else None
            in_bar = last is not None and last >= minute
            staleness = r"\N" if last is None else str((minute - last // 60 * 60) // 60)
            build.rows_written["price_bars"] += 1
            yield (
                f"{tok}\t{_epoch_ts(minute)}\t{samples[minute][2]:.6f}\t"
                f"{'f' if in_bar else 't'}\t{staleness}\tprices_history\n"
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


def rebuild(settings: IngestSettings) -> RebuildResult:
    from syntha_ingestion.quality import build_quality

    raw = RawStore(settings.raw_dir)
    build = BuildLog()

    fees = fee_schedule_rows()
    collections, members, wanted = collection_rows(raw)
    if not wanted:
        raise RuntimeError(f"no collection manifests under {settings.raw_dir}; run `sample` first")
    mb = market_rows(raw, wanted, build)
    trades, prints = trade_lines(raw, mb.tokens, build)
    log.info(
        "markets=%d tokens=%d prints=%d; loading", len(mb.markets), len(mb.tokens), len(trades)
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
            _copy_rows(cur, "collections", "id, name, definition, created_at", collections)
            _copy_rows(cur, "collection_markets", "collection_id, condition_id", members)
            _copy_rows(
                cur, "events", "id, slug, title, category, tags, created_at, ingested_at", mb.events
            )
            _copy_rows(
                cur,
                "markets",
                "condition_id, event_id, question_id, slug, question, category, neg_risk, "
                "tick_size, min_order_size, fees_enabled, fee_type, fee_rate, fee_exponent, "
                "created_at, open_ts, end_date, closed_time, resolved_at, resolution, "
                "final_volume, ingested_at, deadline_type, neg_risk_other",
                mb.markets,
            )
            _copy_rows(
                cur,
                "outcomes",
                "clob_token_id, condition_id, outcome_index, outcome_name",
                mb.outcomes,
            )
            _copy_lines(
                cur,
                "trade_prints",
                "ts, print_key, condition_id, clob_token_id, price, size, side, proxy_wallet",
                trades,
            )
            n_bars = _copy_lines(
                cur,
                "price_bars",
                "clob_token_id, ts, price, is_stale, staleness_minutes, source",
                bar_lines(raw, mb.tokens, prints, build),
            )
            log.info("price_bars loaded: %d; hashing", n_bars)

            digest = content_hash(conn)
            quality = build_quality(conn, build.as_dict())
            counts = {
                "markets": len(mb.markets),
                "tokens": len(mb.tokens),
                "bars": n_bars,
                "prints": len(trades),
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
    return RebuildResult(version_id, digest, counts, quality)


def verify(settings: IngestSettings) -> tuple[str, str | None]:
    """(hash of the current normalized tables, content_hash of the latest dataset version)."""
    with psycopg.connect(psycopg_url(settings.database_url)) as conn:
        _session(conn)
        latest = conn.execute(
            "SELECT content_hash FROM dataset_versions ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
        return content_hash(conn), (latest[0] if latest else None)
