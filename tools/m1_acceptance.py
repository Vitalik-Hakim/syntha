#!/usr/bin/env python3
"""M1 acceptance check (PRD §18).

1. `make rebuild-normalized` reproduces the normalized layer bit-identically: rebuild twice and
   compare content hashes.
2. 500+ markets have their full 1-minute price history in Postgres. "Full" is defined per token
   and measured rather than assumed: the bar series starts within `EDGE_MINUTES` of the market's
   open, ends within `EDGE_MINUTES` of when trading closed (or of the last bar for open markets),
   and has no gap longer than `MAX_GAP_MINUTES`. A market passes when both tokens pass.

Exits non-zero if either criterion fails, and prints why.
"""

from __future__ import annotations

import json
import sys

import psycopg

from syntha_ingestion.config import IngestSettings, psycopg_url
from syntha_ingestion.normalize import rebuild

REQUIRED_MARKETS = 500
EDGE_MINUTES = 60
MAX_GAP_MINUTES = 60

COVERAGE_SQL = """
WITH gaps AS (
  SELECT clob_token_id,
         max(extract(epoch FROM ts - prev) / 60) AS max_gap,
         min(ts) AS first_bar, max(ts) AS last_bar, count(*) AS bars
  FROM (SELECT clob_token_id, ts, lag(ts) OVER (PARTITION BY clob_token_id ORDER BY ts) AS prev
        FROM price_bars) b
  GROUP BY clob_token_id
), tokens AS (
  SELECT o.condition_id, o.clob_token_id, g.bars, g.max_gap,
         extract(epoch FROM g.first_bar - m.open_ts) / 60 AS start_lag,
         CASE WHEN m.closed_time IS NULL THEN 0
              ELSE extract(epoch FROM m.closed_time - g.last_bar) / 60 END AS end_lag,
         (g.bars IS NOT NULL
          AND m.open_ts IS NOT NULL
          AND extract(epoch FROM g.first_bar - m.open_ts) / 60 <= %(edge)s
          AND (m.closed_time IS NULL OR extract(epoch FROM m.closed_time - g.last_bar) / 60 <= %(edge)s)
          AND coalesce(g.max_gap, 1) <= %(gap)s) AS full_history
  FROM outcomes o
  JOIN markets m ON m.condition_id = o.condition_id
  LEFT JOIN gaps g ON g.clob_token_id = o.clob_token_id
)
SELECT
  count(DISTINCT condition_id) AS markets,
  count(DISTINCT condition_id) FILTER (WHERE condition_id NOT IN
      (SELECT condition_id FROM tokens WHERE NOT full_history)) AS markets_full_history,
  count(*) FILTER (WHERE bars IS NULL) AS tokens_without_bars,
  count(*) FILTER (WHERE start_lag > %(edge)s) AS tokens_late_start,
  count(*) FILTER (WHERE end_lag > %(edge)s) AS tokens_early_end,
  count(*) FILTER (WHERE max_gap > %(gap)s) AS tokens_with_long_gap
FROM tokens
"""


def main() -> int:
    settings = IngestSettings()
    print("rebuild 1/2 …", flush=True)
    first = rebuild(settings)
    print(f"  hash {first.content_hash}  {json.dumps(first.counts)}", flush=True)
    print("rebuild 2/2 …", flush=True)
    second = rebuild(settings)
    print(f"  hash {second.content_hash}", flush=True)
    identical = first.content_hash == second.content_hash

    with psycopg.connect(psycopg_url(settings.database_url)) as conn:
        cur = conn.execute(COVERAGE_SQL, {"edge": EDGE_MINUTES, "gap": MAX_GAP_MINUTES})
        cols = [d.name for d in cur.description or []]
        row = cur.fetchone()
        coverage = dict(zip(cols, row or (), strict=True))

    print(
        json.dumps(
            {
                "bit_identical_rebuild": identical,
                "coverage": coverage,
                "definition": {"edge_minutes": EDGE_MINUTES, "max_gap_minutes": MAX_GAP_MINUTES},
            },
            indent=2,
            default=str,
        )
    )
    ok = identical and coverage["markets_full_history"] >= REQUIRED_MARKETS
    print("M1 ACCEPTANCE: " + ("PASS" if ok else "FAIL"))
    if not identical:
        print("  rebuild hashes differ", file=sys.stderr)
    if coverage["markets_full_history"] < REQUIRED_MARKETS:
        print(
            f"  only {coverage['markets_full_history']} markets have full 1-minute history "
            f"(need {REQUIRED_MARKETS})",
            file=sys.stderr,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
