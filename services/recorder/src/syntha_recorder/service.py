"""Always-on L2 recorder (PRD §4.4).

Subscribes to the CLOB market websocket for a watchlist, appends every frame to the raw layer
(`raw/ws_market/<date>/<hour>.jsonl`, plain JSONL because it is appended continuously), and
writes book snapshots and level deltas to Postgres in batches. It reconnects with backoff and
refreshes the watchlist periodically. This data cannot be backfilled, so the recorder keeps
retrying rather than exiting on errors.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import websockets

from syntha_recorder import disk
from syntha_recorder.parse import DeltaRow, SnapshotRow, events, rows

log = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL_S = 10  # documented application-level heartbeat


@dataclass
class RecorderConfig:
    database_url: str
    raw_dir: Path
    flush_seconds: float = 2.0
    watchlist_refresh_seconds: float = 3600.0
    ws_url: str = WS_URL
    retention_days: int | None = None  # drop hypertable chunks older than this at each refresh
    disk_cap_bytes: int | None = None  # hard cap on hypertables + raw/ws_market; see disk.py


@dataclass
class Buffers:
    snapshots: list[SnapshotRow] = field(default_factory=list)
    deltas: list[DeltaRow] = field(default_factory=list)


class RawFrameLog:
    """Hourly gzip JSONL files, appended as frames arrive.

    Each flush ends a gzip block, so a crash loses at most the frames since the last flush and
    the file stays readable (`gzip -dc`). Frames compress roughly 10x.
    """

    def __init__(self, raw_dir: Path) -> None:
        self.base = raw_dir / "ws_market"
        self._path: Path | None = None
        self._fh: Any = None

    def write(self, frame: str, received_at: datetime) -> None:
        hour = received_at.strftime("%H")
        path = self.base / received_at.strftime("%Y-%m-%d") / f"{hour}.jsonl.gz"
        if path != self._path:
            self.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = gzip.open(path, "at", encoding="utf-8")  # noqa: SIM115 - long-lived, closed in close()
            self._path = path
        self._fh.write(json.dumps({"received_at": received_at.isoformat(), "frame": frame}) + "\n")

    def flush(self) -> None:
        if self._fh:
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def write_rows(conn: psycopg.Connection[Any], buf: Buffers) -> tuple[int, int]:
    snaps, deltas = buf.snapshots, buf.deltas
    buf.snapshots, buf.deltas = [], []
    with conn.transaction(), conn.cursor() as cur:
        if snaps:
            cur.executemany(
                """INSERT INTO orderbook_snapshots
                     (clob_token_id, ts, condition_id, bids, asks, book_hash, received_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (clob_token_id, ts) DO NOTHING""",
                [
                    (
                        s.clob_token_id,
                        s.ts,
                        s.condition_id,
                        json.dumps(s.bids),
                        json.dumps(s.asks),
                        s.book_hash,
                        s.received_at,
                    )
                    for s in snaps
                ],
            )
        if deltas:
            with cur.copy(
                "COPY orderbook_deltas (ts, clob_token_id, condition_id, side, price, size, "
                "best_bid, best_ask, received_at, event_type, fee_rate_bps) FROM STDIN"
            ) as cp:
                for d in deltas:
                    cp.write_row(
                        (
                            d.ts,
                            d.clob_token_id,
                            d.condition_id,
                            d.side,
                            d.price,
                            d.size,
                            d.best_bid,
                            d.best_ask,
                            d.received_at,
                            d.event_type,
                            d.fee_rate_bps,
                        )
                    )
    return len(snaps), len(deltas)


WatchlistSource = Callable[[], Awaitable[set[str]]]


async def run(
    config: RecorderConfig, watchlist: WatchlistSource, *, stop: asyncio.Event | None = None
) -> None:
    stop = stop or asyncio.Event()
    raw = RawFrameLog(config.raw_dir)
    buf = Buffers()
    backoff = 1.0
    conn = await asyncio.to_thread(_connect, config.database_url)
    try:
        while not stop.is_set():
            try:
                watched = await watchlist()
                if not watched:
                    log.warning("watchlist is empty; retrying in 60s")
                    await _sleep_or_stop(stop, 60)
                    continue
                await _session(config, conn, raw, buf, watched, watchlist, stop)
                backoff = 1.0
            except (OSError, websockets.WebSocketException, psycopg.OperationalError) as exc:
                wait = min(300.0, backoff) + random.uniform(0, 1)
                log.warning("recorder connection lost (%s); reconnecting in %.0fs", exc, wait)
                backoff *= 2
                if conn.closed:
                    conn = await asyncio.to_thread(_connect, config.database_url)
                await _sleep_or_stop(stop, wait)
    finally:
        if buf.snapshots or buf.deltas:
            await asyncio.to_thread(write_rows, conn, buf)
        raw.close()
        conn.close()


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


def _connect(url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(url, autocommit=False)


async def _session(
    config: RecorderConfig,
    conn: psycopg.Connection[Any],
    raw: RawFrameLog,
    buf: Buffers,
    watched: set[str],
    watchlist: WatchlistSource,
    stop: asyncio.Event,
) -> None:
    async with websockets.connect(config.ws_url, ping_interval=None, max_size=2**24) as ws:
        await ws.send(
            json.dumps(
                {"assets_ids": sorted(watched), "type": "market", "custom_feature_enabled": True}
            )
        )
        log.info("recording %d tokens", len(watched))
        now = time.monotonic()
        last_ping = last_flush = last_refresh = now
        totals = [0, 0]
        while not stop.is_set():
            now = time.monotonic()
            if now - last_ping >= PING_INTERVAL_S:
                await ws.send("PING")
                last_ping = now
            if now - last_flush >= config.flush_seconds:
                raw.flush()
                if buf.snapshots or buf.deltas:
                    s, d = await asyncio.to_thread(write_rows, conn, buf)
                    totals[0] += s
                    totals[1] += d
                last_flush = now
            if now - last_refresh >= config.watchlist_refresh_seconds:
                if config.disk_cap_bytes is not None or config.retention_days is not None:
                    decision = await asyncio.to_thread(
                        disk.enforce,
                        conn,
                        config.raw_dir,
                        retention_days=config.retention_days or 10**6,
                        cap_bytes=config.disk_cap_bytes or 2**62,
                    )
                    if decision == "stop":
                        stop.set()
                        break
                new = await watchlist()
                added, removed = new - watched, watched - new
                if added:
                    await ws.send(
                        json.dumps({"assets_ids": sorted(added), "operation": "subscribe"})
                    )
                if removed:
                    await ws.send(
                        json.dumps({"assets_ids": sorted(removed), "operation": "unsubscribe"})
                    )
                watched = new
                last_refresh = now
                log.info(
                    "watchlist refreshed: +%d -%d; totals snapshots=%d deltas=%d",
                    len(added),
                    len(removed),
                    *totals,
                )
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                continue
            if not isinstance(frame, str):
                continue
            received_at = datetime.now(UTC)
            raw.write(frame, received_at)
            for event in events(frame):
                snaps, deltas = rows(event, received_at, watched)
                buf.snapshots.extend(snaps)
                buf.deltas.extend(deltas)
