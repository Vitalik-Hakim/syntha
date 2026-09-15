"""Test support: a tiny, fully known dataset in a real database (used by worker and API tests)."""

from __future__ import annotations

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[4] / "packages" / "storage" / "alembic.ini"


def seed_minimal_dataset(url: str) -> str:
    """Migrate `url` to head, replace its data with one resolved market, return a psycopg URL.

    DESTRUCTIVE: truncates normalized and run tables. Only ever point this at a test database.
    """
    if "test" not in url:
        raise RuntimeError(f"refusing to seed a database whose URL does not contain 'test': {url}")
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    plain = url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(plain) as conn:
        conn.execute(
            "TRUNCATE fee_schedules, collections, collection_markets, events, markets, outcomes, "
            "trade_prints, price_bars, dataset_versions, backtest_runs, strategy_versions, "
            "strategies RESTART IDENTITY CASCADE"
        )
        conn.execute(
            """INSERT INTO collections VALUES ('00000000-0000-0000-0000-000000000001', 'c1', '{}', now())"""
        )
        conn.execute(
            """INSERT INTO collection_markets VALUES ('00000000-0000-0000-0000-000000000001', 'M1')"""
        )
        conn.execute("""INSERT INTO markets (condition_id, slug, question, category, tick_size, min_order_size,
                        fees_enabled, fee_type, fee_rate, fee_exponent, created_at, open_ts, end_date, closed_time,
                        resolved_at, resolution, final_volume, ingested_at)
                        VALUES ('M1', 's', 'q?', 'sports', 0.01, 5, true, 'sports_fees_v2', 0.03, 1,
                                '2026-01-01', '2026-01-01', '2026-01-02', '2026-01-01 00:10+00', '2026-01-01 00:12+00',
                                'YES', 99999, now()),
                               ('M2', 's2', 'q2?', 'sports', 0.01, 5, false, null, null, null,
                                '2026-01-01', '2026-01-01', null, null, null, 'UNRESOLVED', 1, now())""")
        conn.execute(
            """INSERT INTO outcomes VALUES ('Y1', 'M1', 0, 'Yes'), ('N1', 'M1', 1, 'No'), ('Y2', 'M2', 0, 'Yes')"""
        )
        conn.execute("""INSERT INTO price_bars (clob_token_id, ts, price, is_stale, source)
                        SELECT 'Y1', '2026-01-01'::timestamptz + make_interval(mins => g), 0.40, true, 'prices_history'
                        FROM generate_series(0, 9) g""")
        conn.execute("""INSERT INTO price_bars (clob_token_id, ts, price, is_stale, source)
                        SELECT 'N1', '2026-01-01'::timestamptz + make_interval(mins => g), 0.60, true, 'prices_history'
                        FROM generate_series(0, 9) g""")
        conn.execute("""INSERT INTO trade_prints (ts, print_key, condition_id, clob_token_id, price, size, side, proxy_wallet) VALUES
                        ('2025-12-31 23:00+00', 'k0', 'M1', 'Y1', 0.5, 100, 'BUY', NULL),
                        ('2026-01-01 00:02:10+00', 'k1', 'M1', 'Y1', 0.41, 200, 'BUY', '0xwallet-a'),
                        ('2026-01-01 00:03:10+00', 'k2', 'M1', 'Y1', 0.42, 300, 'SELL', '0xwallet-a')""")
        conn.execute("""INSERT INTO fee_schedules (venue, fee_type, rate, exponent, applies_to, effective_from, source_url)
                        VALUES ('international', 'sports_fees_v2', 0.03, 1, 'ALL_OPEN_MARKETS', '2025-01-01', 'x')""")
        conn.execute("""INSERT INTO dataset_versions (id, built_at, content_hash) VALUES
                        ('00000000-0000-0000-0000-0000000000aa', now(), 'hash-1')""")
    return plain
