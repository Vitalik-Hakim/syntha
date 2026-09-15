"""Recorder coverage report (PRD v3 §3.7, E7): token-days recorded, gaps > 5 min, bytes/day."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg

GAP = "5 minutes"


def report(database_url: str, raw_dir: Path, days: int) -> dict[str, Any]:
    with psycopg.connect(database_url) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        daily = conn.execute(
            """
            SELECT date(ts), count(DISTINCT clob_token_id), count(*),
                   count(*) FILTER (WHERE event_type = 'trade')
            FROM orderbook_deltas
            WHERE ts >= now() - make_interval(days => %s)
            GROUP BY 1 ORDER BY 1
            """,
            (days,),
        ).fetchall()
        gaps: dict[Any, int] = dict(
            conn.execute(
                f"""
            WITH t AS (
              SELECT clob_token_id, ts,
                     lag(ts) OVER (PARTITION BY clob_token_id ORDER BY ts) AS prev
              FROM orderbook_deltas WHERE ts >= now() - make_interval(days => %s)
            )
            SELECT date(ts), count(*) FILTER (WHERE ts - prev > interval '{GAP}')
            FROM t GROUP BY 1
            """,
                (days,),
            ).fetchall()
        )
        watch = conn.execute(
            "SELECT stratum, count(DISTINCT condition_id), count(*) FROM recorder_watchlist "
            "WHERE removed_at IS NULL GROUP BY 1 ORDER BY 1"
        ).fetchall()
        try:
            size = conn.execute(
                "SELECT hypertable_size('orderbook_deltas') + hypertable_size('orderbook_snapshots')"
            ).fetchone()
            db_bytes = None if size is None else int(size[0])
        except psycopg.Error:
            conn.rollback()
            db_bytes = None
    rows = []
    for day, tokens, n_rows, n_trades in daily:
        folder = raw_dir / "ws_market" / day.strftime("%Y-%m-%d")
        raw_bytes = sum(f.stat().st_size for f in folder.glob("*.jsonl*")) if folder.exists() else 0
        rows.append(
            {
                "day": day.isoformat(),
                "tokens": tokens,
                "rows": n_rows,
                "trade_rows": n_trades,
                "gaps_over_5min": int(gaps.get(day, 0)),
                "raw_bytes": raw_bytes,
            }
        )
    return {
        "days": days,
        "token_days": sum(r["tokens"] for r in rows),
        "daily": rows,
        "watchlist": [{"stratum": s, "markets": int(m), "tokens": int(t)} for s, m, t in watch],
        "db_bytes": db_bytes,
    }


def render(rep: dict[str, Any]) -> str:
    lines = [f"Recorder coverage, last {rep['days']} days: {rep['token_days']} token-days"]
    lines.append(
        f"{'day':10s} {'tokens':>6s} {'rows':>10s} {'trades':>8s} {'gaps>5m':>8s} {'raw MB':>8s}"
    )
    for r in rep["daily"]:
        lines.append(
            f"{r['day']:10s} {r['tokens']:6d} {r['rows']:10d} {r['trade_rows']:8d} "
            f"{r['gaps_over_5min']:8d} {r['raw_bytes'] / 1e6:8.1f}"
        )
    if rep["db_bytes"] is not None:
        lines.append(
            f"recorder hypertables: {rep['db_bytes'] / 1e9:.2f} GB (compressed after 1 day)"
        )
    lines.append("watchlist (active):")
    for w in rep["watchlist"]:
        lines.append(f"  {w['stratum']:28s} {w['markets']:3d} markets {w['tokens']:3d} tokens")
    if not rep["watchlist"]:
        lines.append("  (empty — run the recorder once to fill it)")
    return "\n".join(lines)
