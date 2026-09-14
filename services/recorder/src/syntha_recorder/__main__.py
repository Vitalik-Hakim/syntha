"""`python -m syntha_recorder` — record the live order book for a watchlist.

Watchlist sources (first match wins):
  --tokens ID,ID,…      explicit token ids
  --watchlist FILE      JSON file {"token_ids": [...]}, re-read on every refresh
  --top N (default 10)  both tokens of the N active markets with the highest 24h volume,
                        re-selected on every refresh

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

from syntha_recorder.service import RecorderConfig, WatchlistSource, run

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"


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
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--refresh-minutes", type=float, default=60)
    p.add_argument("--flush-seconds", type=float, default=2)
    args = p.parse_args()

    if args.tokens:
        source = static_source({t.strip() for t in args.tokens.split(",") if t.strip()})
    elif args.watchlist:
        source = file_source(args.watchlist)
    else:
        source = top_markets_source(args.top)

    db = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    config = RecorderConfig(
        database_url=db.replace("postgresql+psycopg://", "postgresql://", 1),
        raw_dir=Path(os.environ.get("SYNTHA_DATA_DIR", "data")) / "raw",
        flush_seconds=args.flush_seconds,
        watchlist_refresh_seconds=args.refresh_minutes * 60,
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
