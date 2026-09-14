"""Calibration of Polymarket prices against outcomes, for hypothesis generation (not a backtest).

For every binary market in a collection, and both of its tokens, take the last sampled price at
fixed offsets from the scheduled end, and compare the average price in each price bucket with the
share of those tokens that paid out. A bucket where tokens pay out more often than their price is
where buying looks cheap; costs, liquidity and timing are left to the engine.

This reads resolution data, so run it only on a discovery collection. Strategies chosen from it
must be validated on a collection it never saw.

Usage: uv run python tools/research/calibration.py <collection> [--min-volume 10000]
"""

from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict

import psycopg

OFFSETS_HOURS = (-72.0, -24.0, -6.0, -2.0, -1.0, -0.25, 0.25, 1.0)
BUCKETS = (0.0, 0.03, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.97, 1.0001)
MAX_STALE_MINUTES = 60

QUERY = """
WITH m AS (
  SELECT mk.condition_id, mk.end_date, mk.closed_time, mk.resolution, mk.category
  FROM collections c JOIN collection_markets cm ON cm.collection_id = c.id
  JOIN markets mk ON mk.condition_id = cm.condition_id
  WHERE c.name = %(collection)s AND mk.end_date IS NOT NULL
    AND mk.resolution IN ('YES', 'NO', 'OUTCOME_0', 'OUTCOME_1', 'INVALID')
), probe AS (
  SELECT m.*, o.clob_token_id, o.outcome_index, off.h,
         m.end_date + make_interval(secs => off.h * 3600) AS at
  FROM m JOIN outcomes o ON o.condition_id = m.condition_id
  CROSS JOIN unnest(%(offsets)s::float8[]) AS off(h)
)
SELECT p.condition_id, p.category, p.outcome_index, p.resolution, p.h, b.price,
       extract(epoch FROM (p.at - b.ts)) / 60 AS stale_min,
       (SELECT coalesce(sum(t.price * t.size), 0) FROM trade_prints t
         WHERE t.clob_token_id = p.clob_token_id AND t.ts <= p.at) AS notional_asof
FROM probe p
JOIN LATERAL (
  SELECT ts, price FROM price_bars pb
  WHERE pb.clob_token_id = p.clob_token_id AND pb.ts <= p.at
  ORDER BY ts DESC LIMIT 1
) b ON true
WHERE p.closed_time IS NULL OR p.at < p.closed_time
"""


def payout(resolution: str, outcome_index: int) -> float:
    if resolution == "INVALID":
        return 0.5
    winner = 0 if resolution in ("YES", "OUTCOME_0") else 1
    return 1.0 if outcome_index == winner else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("collection")
    ap.add_argument("--min-volume", type=float, default=10_000.0)
    ap.add_argument("--category")
    args = ap.parse_args()
    url = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            QUERY, {"collection": args.collection, "offsets": list(OFFSETS_HOURS)}
        ).fetchall()

    cells: dict[tuple[float, int], list[tuple[float, float]]] = defaultdict(list)
    markets: set[str] = set()
    for cid, category, idx, resolution, h, price, stale, notional in rows:
        if stale > MAX_STALE_MINUTES or float(notional) < args.min_volume:
            continue
        if args.category and (category or "").lower() != args.category.lower():
            continue
        p = float(price)
        b = next(i for i in range(len(BUCKETS) - 1) if BUCKETS[i] <= p < BUCKETS[i + 1])
        cells[(h, b)].append((p, payout(resolution, idx)))
        markets.add(cid)

    print(
        f"{args.collection}: {len(markets)} markets with a fresh price and ≥ ${args.min_volume:,.0f} traded"
    )
    print("edge = mean payout − mean price (before any cost); ± is one standard error\n")
    header = "bucket        " + "".join(f"{h:>+17.2f}h" for h in OFFSETS_HOURS)
    print(header)
    for b in range(len(BUCKETS) - 1):
        line = f"{BUCKETS[b]:.2f}–{min(BUCKETS[b + 1], 1):.2f}    "
        for h in OFFSETS_HOURS:
            obs = cells.get((h, b), [])
            if len(obs) < 5:
                line += f"{'·':>18}"
                continue
            n = len(obs)
            mp = sum(o[0] for o in obs) / n
            mo = sum(o[1] for o in obs) / n
            se = math.sqrt(max(mo * (1 - mo), 1e-9) / n)
            line += f"{mo - mp:>+8.3f}±{se:.3f} n{n:<4d}"
        print(line)


if __name__ == "__main__":
    main()
