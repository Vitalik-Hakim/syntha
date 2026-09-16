"""Retention and a hard disk cap for the recorder (PRD v3 E7 operations).

The recorder's footprint is the two order-book hypertables plus `raw/ws_market` (gzip JSONL).
Retention drops hypertable chunks older than `retention_days` at every watchlist refresh. The
cap compares the whole footprint with `cap_bytes`: hypertable chunks are dropped oldest-first
until the footprint fits (they are re-derivable from raw); if the raw frames alone exceed the
cap, recording stops — `data/raw/` is append-only and is never deleted (CLAUDE.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

log = logging.getLogger(__name__)

TABLES = ("orderbook_snapshots", "orderbook_deltas")


@dataclass(frozen=True)
class Footprint:
    db_bytes: int
    raw_bytes: int

    @property
    def total(self) -> int:
        return self.db_bytes + self.raw_bytes


def decide(fp: Footprint, cap_bytes: int) -> str:
    """'ok' (under the cap), 'drop' (over: drop the oldest chunk), or 'stop' (the raw frames
    alone exceed the cap: nothing left to drop). Pure, so it is testable."""
    if fp.total <= cap_bytes:
        return "ok"
    return "stop" if fp.raw_bytes > cap_bytes else "drop"


def raw_size(raw_dir: Path) -> int:
    base = raw_dir / "ws_market"
    return sum(f.stat().st_size for f in base.rglob("*") if f.is_file()) if base.exists() else 0


def db_size(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute("SELECT " + " + ".join(f"hypertable_size('{t}')" for t in TABLES)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def apply_retention(conn: psycopg.Connection[Any], retention_days: int) -> int:
    """Drop chunks older than `retention_days`; returns how many chunks went."""
    n = 0
    with conn.transaction():
        for t in TABLES:
            rows = conn.execute(
                f"SELECT drop_chunks('{t}', older_than => interval '{int(retention_days)} days')"
            ).fetchall()
            n += len(rows)
    return n


def drop_oldest_chunk(conn: psycopg.Connection[Any]) -> bool:
    row = conn.execute(
        """SELECT format('%I.%I', chunk_schema, chunk_name), range_end
           FROM timescaledb_information.chunks
           WHERE hypertable_name = ANY(%s) ORDER BY range_end LIMIT 1""",
        (list(TABLES),),
    ).fetchone()
    if row is None:
        return False
    with conn.transaction():
        conn.execute(f"DROP TABLE {row[0]}")
    log.warning("disk cap: dropped chunk %s (up to %s)", row[0], row[1])
    return True


def enforce(
    conn: psycopg.Connection[Any], raw_dir: Path, *, retention_days: int, cap_bytes: int
) -> str:
    """One pass: retention, then the cap. Returns the final decision ('ok' or 'stop')."""
    dropped = apply_retention(conn, retention_days)
    if dropped:
        log.info("retention: dropped %d chunk(s) older than %d days", dropped, retention_days)
    for _ in range(1000):
        fp = Footprint(db_size(conn), raw_size(raw_dir))
        d = decide(fp, cap_bytes)
        if d == "ok":
            return "ok"
        if d == "stop" or not drop_oldest_chunk(conn):
            log.error(
                "disk cap %.1f GB exceeded by raw frames alone (%.1f GB); recording stops. "
                "data/raw is append-only: move it elsewhere or raise the cap.",
                cap_bytes / 1e9,
                fp.raw_bytes / 1e9,
            )
            return "stop"
    return "stop"
