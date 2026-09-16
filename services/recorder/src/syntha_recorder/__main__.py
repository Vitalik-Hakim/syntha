"""`python -m syntha_recorder` — record the live order book for a watchlist.

Watchlist sources (first match wins):
  --tokens ID,ID,…      explicit token ids
  --watchlist FILE      JSON file {"token_ids": [...]}, re-read on every refresh
  --top N               both tokens of the N active markets with the highest 24h volume (v2)
  (default)             the designed, stratified watchlist in `recorder_watchlist` (PRD v3 E7):
                        4 categories × {binary, multi} × {thin, liquid}, --target-per-stratum
                        markets each, refilled on every refresh

Other modes:
  --coverage [--days N] print the coverage report (token-days, gaps > 5 min, bytes/day) and exit
  --add CID,CID [--reason manual|structural_candidate]  watch specific markets and exit

Choosing what to *record* by current volume is fine: it only decides which future data exists.
Backtest universes must still be selected point-in-time (PRD §5.1).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path

import httpx
import psycopg

from syntha_recorder import coverage, watchlist
from syntha_recorder.service import RecorderConfig, WatchlistSource, run

GAMMA_MARKETS = watchlist.GAMMA_MARKETS


def top_markets_source(n: int) -> WatchlistSource:
    async def load() -> set[str]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                GAMMA_MARKETS,
                params={
                    "closed": "false",
                    "active": "true",
                    "limit": min(n, 100),
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            resp.raise_for_status()
        tokens: set[str] = set()
        for m in resp.json()[:n]:
            if m.get("enableOrderBook") is False:
                continue
            tokens.update(str(t) for t in json.loads(m.get("clobTokenIds") or "[]"))
        return tokens

    return load


def file_source(path: Path) -> WatchlistSource:
    async def load() -> set[str]:
        return {str(t) for t in json.loads(path.read_text())["token_ids"]}

    return load


def static_source(tokens: set[str]) -> WatchlistSource:
    async def load() -> set[str]:
        return tokens

    return load


def designed_source(
    database_url: str, target: int, categories: tuple[str, ...], final_24h_slots: int
) -> WatchlistSource:
    """The v3 E7 watchlist: refill the table, then record everything active in it."""

    async def load() -> set[str]:
        return await asyncio.to_thread(
            watchlist.refresh,
            database_url,
            target=target,
            categories=categories,
            final_24h_slots=final_24h_slots,
        )

    return load


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(
        prog="syntha-recorder",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tokens")
    p.add_argument("--watchlist", type=Path)
    p.add_argument("--top", type=int, help="v2 behaviour: top N markets by 24h volume")
    p.add_argument("--target-per-stratum", type=int, default=watchlist.TARGET_PER_STRATUM)
    p.add_argument("--categories", default=",".join(watchlist.DEFAULT_CATEGORIES))
    p.add_argument("--final-24h-slots", type=int, default=watchlist.FINAL_24H_SLOTS)
    p.add_argument("--retention-days", type=int, default=30)
    p.add_argument("--disk-cap-gb", type=float, default=20.0)
    p.add_argument("--refresh-minutes", type=float, default=60)
    p.add_argument("--flush-seconds", type=float, default=2)
    p.add_argument("--coverage", action="store_true")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--add", help="comma-separated condition ids to watch")
    p.add_argument("--reason", default="manual", choices=["manual", "structural_candidate"])
    args = p.parse_args()

    db = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    db = db.replace("postgresql+psycopg://", "postgresql://", 1)
    raw_dir = Path(os.environ.get("SYNTHA_DATA_DIR", "data")) / "raw"

    if args.coverage:
        print(coverage.render(coverage.report(db, raw_dir, args.days)))
        return
    if args.add:
        ids = [c.strip() for c in args.add.split(",") if c.strip()]
        with psycopg.connect(db) as conn, httpx.Client(timeout=30) as client:
            n = watchlist.add_manual(conn, client, ids, args.reason)
        print(f"added {n} market(s) as {args.reason}")
        return

    categories = tuple(c.strip() for c in args.categories.split(",") if c.strip())
    if args.tokens:
        source = static_source({t.strip() for t in args.tokens.split(",") if t.strip()})
    elif args.watchlist:
        source = file_source(args.watchlist)
    elif args.top:
        source = top_markets_source(args.top)
    else:
        source = designed_source(db, args.target_per_stratum, categories, args.final_24h_slots)

    config = RecorderConfig(
        database_url=db,
        raw_dir=raw_dir,
        flush_seconds=args.flush_seconds,
        watchlist_refresh_seconds=args.refresh_minutes * 60,
        retention_days=args.retention_days,
        disk_cap_bytes=int(args.disk_cap_gb * 1e9),
    )

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run(config, source, stop=stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
