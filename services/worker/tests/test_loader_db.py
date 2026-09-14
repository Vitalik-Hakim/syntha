"""DB loader: restricted columns reach only the SettlementTable, only the traded side is loaded,
and a loaded dataset runs end to end. Requires SYNTHA_TEST_DATABASE_URL."""

import dataclasses
import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from syntha_contracts.backtest import BacktestRequest
from syntha_engine.data import MarketTerms
from syntha_engine.runner import run
from syntha_worker.loader import load_dataset

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages" / "storage" / "alembic.ini"


@pytest.fixture
def db() -> str:
    url = os.environ.get("SYNTHA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SYNTHA_TEST_DATABASE_URL not set")
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    plain = url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(plain) as conn:
        conn.execute(
            "TRUNCATE fee_schedules, collections, collection_markets, events, markets, outcomes, "
            "trade_prints, price_bars, dataset_versions RESTART IDENTITY CASCADE"
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
        conn.execute("""INSERT INTO trade_prints (ts, print_key, condition_id, clob_token_id, price, size, side) VALUES
                        ('2025-12-31 23:00+00', 'k0', 'M1', 'Y1', 0.5, 100, 'BUY'),
                        ('2026-01-01 00:02:10+00', 'k1', 'M1', 'Y1', 0.41, 200, 'BUY'),
                        ('2026-01-01 00:03:10+00', 'k2', 'M1', 'Y1', 0.42, 300, 'SELL')""")
        conn.execute("""INSERT INTO fee_schedules (venue, fee_type, rate, exponent, applies_to, effective_from, source_url)
                        VALUES ('international', 'sports_fees_v2', 0.03, 1, 'ALL_OPEN_MARKETS', '2025-01-01', 'x')""")
        conn.execute("""INSERT INTO dataset_versions (id, built_at, content_hash) VALUES
                        ('00000000-0000-0000-0000-0000000000aa', now(), 'hash-1')""")
    return plain


def _request(**dataset: object) -> BacktestRequest:
    return BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "t",
                "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.5}]},
                "sizing": {"mode": "FIXED_CASH", "value": 5},
            },
            "dataset": {"collection": "c1", **dataset},
            "execution": {
                "model": "LAST_PRICE",
                "slippage_ticks": 0,
                "liquidity_participation": None,
            },
        }
    )


def test_restricted_fields_only_in_settlement_and_only_traded_side_loaded(db: str) -> None:
    loaded = load_dataset(db, _request(start="2026-01-01T00:00:00Z"))
    ds = loaded.dataset
    assert set(ds.markets) == {"M1"}  # collection filter
    assert set(ds.tokens) == {"Y1"}  # BUY_YES loads outcome 0 only
    field_names = {f.name for f in dataclasses.fields(MarketTerms)}
    assert field_names.isdisjoint({"resolution", "resolved_at", "closed_time", "final_volume"})
    s = loaded.settlement.get("M1")
    assert s.resolution == "YES" and s.resolved_ts is not None and s.closed_ts is not None
    tok = ds.tokens["Y1"]
    assert len(tok.bar_ts) == 10 and len(tok.print_ts) == 2
    assert tok.prior_notional == pytest.approx(
        50.0
    )  # the print before `start` counts toward as-of volume
    assert ds.fee_schedules and ds.dataset_version_id == "00000000-0000-0000-0000-0000000000aa"
    assert loaded.content_hash == "hash-1"


def test_loaded_dataset_runs_end_to_end(db: str) -> None:
    loaded = load_dataset(db, _request())
    result = run(_request(), loaded.dataset, loaded.settlement)
    assert result.trades and result.trades[0]["fill_status"] == "FILLED"
    assert result.trades[0]["exit_reason"] == "MARKET_RESOLVED"
