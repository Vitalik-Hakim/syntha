"""Dataset-level data-quality report, computed from the normalized tables.

Stored on `dataset_versions.quality` and printed by `syntha-ingest report`. Everything is
measured, not assumed; see docs/DATA_NOTES.md for why each metric exists.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg


def _num(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return v


def _one(conn: psycopg.Connection[Any], sql: str) -> dict[str, Any]:
    cur = conn.execute(sql)
    row = cur.fetchone()
    cols = [d.name for d in cur.description or []]
    return {c: _num(v) for c, v in zip(cols, row or [None] * len(cols), strict=True)}


def _all(conn: psycopg.Connection[Any], sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d.name for d in cur.description or []]
    return [{c: _num(v) for c, v in zip(cols, row, strict=True)} for row in cur.fetchall()]


def build_quality(conn: psycopg.Connection[Any], build_log: dict[str, Any]) -> dict[str, Any]:
    q: dict[str, Any] = {"build": build_log}

    q["totals"] = _one(
        conn,
        """
        SELECT (SELECT count(*) FROM markets)                        AS markets,
               (SELECT count(*) FROM outcomes)                       AS tokens,
               (SELECT count(*) FROM price_bars)                     AS bars,
               (SELECT count(*) FROM trade_prints)                   AS prints,
               (SELECT min(ts) FROM price_bars)::text                AS coverage_start,
               (SELECT max(ts) FROM price_bars)::text                AS coverage_end
    """,
    )

    q["markets_by_category"] = _all(
        conn,
        """
        SELECT coalesce(category, 'unknown') AS category, count(*) AS markets
        FROM markets GROUP BY 1 ORDER BY 1
    """,
    )

    q["resolutions"] = _all(
        conn,
        """
        SELECT resolution, count(*) AS markets,
               count(*) FILTER (WHERE resolved_at IS NULL) AS without_resolved_at
        FROM markets GROUP BY 1 ORDER BY 1
    """,
    )

    q["coverage_gaps"] = _one(
        conn,
        """
        SELECT count(*) FILTER (WHERE bars = 0)   AS tokens_without_bars,
               count(*) FILTER (WHERE prints = 0) AS tokens_without_prints
        FROM (
          SELECT o.clob_token_id,
                 (SELECT count(*) FROM price_bars b WHERE b.clob_token_id = o.clob_token_id) AS bars,
                 (SELECT count(*) FROM trade_prints p WHERE p.clob_token_id = o.clob_token_id) AS prints
          FROM outcomes o
        ) t
    """,
    )

    # Missing minutes inside each token's bar series (the CLOB normally returns every minute).
    q["bar_gaps"] = _one(
        conn,
        """
        WITH g AS (
          SELECT extract(epoch FROM ts - lag(ts) OVER (PARTITION BY clob_token_id ORDER BY ts)) / 60
                 AS gap_minutes
          FROM price_bars
        )
        SELECT count(*) FILTER (WHERE gap_minutes > 1)                    AS gaps_over_1m,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_minutes)   AS median_gap_minutes,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY gap_minutes)  AS p95_gap_minutes,
               max(gap_minutes)                                           AS max_gap_minutes
        FROM g WHERE gap_minutes IS NOT NULL
    """,
    )

    q["staleness"] = _one(
        conn,
        """
        SELECT round(100.0 * avg(is_stale::int), 2)                                    AS bars_with_no_prints_pct,
               round(100.0 * avg((staleness_minutes IS NULL)::int), 2)                 AS bars_before_first_print_pct,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY staleness_minutes)          AS median_staleness_minutes,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY staleness_minutes)         AS p95_staleness_minutes,
               round(100.0 * avg((staleness_minutes > 60)::int), 2)                    AS bars_stale_over_60m_pct
        FROM price_bars
    """,
    )

    # PRD §3.3: YES + NO should be close to 1. On CLOB bars it is almost always exactly 1 (the two
    # series come from one shared book), so the informative version is measured on trade prints.
    q["complement_divergence_prints"] = _one(
        conn,
        """
        WITH p AS (
          SELECT o.condition_id, date_trunc('minute', t.ts) AS minute, o.outcome_index AS i,
                 avg(t.price) AS px
          FROM trade_prints t JOIN outcomes o ON o.clob_token_id = t.clob_token_id
          GROUP BY 1, 2, 3
        ), d AS (
          SELECT abs(a.px + b.px - 1) AS dev
          FROM p a JOIN p b ON b.condition_id = a.condition_id AND b.minute = a.minute
          WHERE a.i = 0 AND b.i = 1
        )
        SELECT count(*)                                            AS minutes_with_both_sides,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY dev)    AS median_abs_dev,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY dev)   AS p95_abs_dev
        FROM d
    """,
    )

    q["complement_divergence_bars"] = _one(
        conn,
        """
        WITH pairs AS (
          SELECT a.condition_id, abs(ba.price + bb.price - 1) AS dev
          FROM outcomes a
          JOIN outcomes b ON b.condition_id = a.condition_id AND a.outcome_index = 0 AND b.outcome_index = 1
          JOIN price_bars ba ON ba.clob_token_id = a.clob_token_id
          JOIN price_bars bb ON bb.clob_token_id = b.clob_token_id AND bb.ts = ba.ts
        )
        SELECT count(*)                                            AS paired_bars,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY dev)    AS median_abs_dev,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY dev)   AS p95_abs_dev,
               round(100.0 * avg((dev > 0.02)::int), 2)            AS over_2c_pct
        FROM pairs
    """,
    )

    # Replaces PRD §17.5's 95% envelope assertion, which cannot hold for mid-like bars.
    q["bar_vs_prints"] = _one(
        conn,
        """
        WITH env AS (
          SELECT clob_token_id, date_trunc('minute', ts) AS minute, min(price) lo, max(price) hi
          FROM trade_prints GROUP BY 1, 2
        ), d AS (
          SELECT greatest(env.lo - b.price, b.price - env.hi, 0) / m.tick_size AS ticks
          FROM env
          JOIN price_bars b ON b.clob_token_id = env.clob_token_id AND b.ts = env.minute
          JOIN outcomes o ON o.clob_token_id = b.clob_token_id
          JOIN markets m ON m.condition_id = o.condition_id
        )
        SELECT count(*)                                               AS bars_with_prints,
               round(100.0 * avg((ticks = 0)::int), 2)                AS inside_envelope_pct,
               round(100.0 * avg((ticks <= 1)::int), 2)               AS within_1_tick_pct,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ticks)     AS median_ticks_outside,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY ticks)     AS p90_ticks_outside
        FROM d
    """,
    )

    q["fees"] = {
        "markets_by_fee_type": _all(
            conn,
            """
            SELECT coalesce(fee_type, 'none') AS fee_type, count(*) AS markets
            FROM markets GROUP BY 1 ORDER BY 1
        """,
        ),
        "fee_types_without_schedule": _all(
            conn,
            """
            SELECT DISTINCT m.fee_type FROM markets m
            WHERE m.fee_type IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM fee_schedules f WHERE f.fee_type = m.fee_type)
            ORDER BY 1
        """,
        ),
        "rate_mismatches": _all(
            conn,
            """
            SELECT m.fee_type, m.fee_rate, m.fee_exponent, f.rate AS schedule_rate,
                   f.exponent AS schedule_exponent, count(*) AS markets
            FROM markets m JOIN fee_schedules f ON f.fee_type = m.fee_type
            WHERE m.fee_rate <> f.rate OR m.fee_exponent <> f.exponent
            GROUP BY 1, 2, 3, 4, 5 ORDER BY 1
        """,
        ),
        "closed_before_schedule_start": _one(
            conn,
            """
            SELECT count(*) AS markets FROM markets m JOIN fee_schedules f ON f.fee_type = m.fee_type
            WHERE m.closed_time < f.effective_from
        """,
        ),
        "assumption": "fees on a market apply from its fee_type's effective_from; before that, zero "
        "(inferred from the changelog, not a per-fill record; docs/DATA_NOTES.md)",
    }

    q["orderbook_coverage"] = _one(
        conn,
        """
        SELECT (SELECT count(*) FROM orderbook_snapshots) AS snapshots,
               (SELECT count(*) FROM orderbook_deltas)    AS deltas
    """,
    )
    return q
