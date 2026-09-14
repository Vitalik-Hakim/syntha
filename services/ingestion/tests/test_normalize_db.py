"""End-to-end rebuild from a synthetic raw layer. Requires SYNTHA_TEST_DATABASE_URL."""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from syntha_ingestion import polymarket as pm
from syntha_ingestion.config import IngestSettings, psycopg_url
from syntha_ingestion.normalize import rebuild, verify
from syntha_ingestion.raw_store import RawStore

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages" / "storage" / "alembic.ini"
COND = "0xc0nd"
YES, NO = "111", "222"
T0 = 1_780_272_000  # 2026-06-01 00:00:00 UTC
FETCH_1 = datetime(2026, 9, 1, tzinfo=UTC)
FETCH_2 = FETCH_1 + timedelta(hours=1)


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("SYNTHA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SYNTHA_TEST_DATABASE_URL not set")
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


def _write(
    raw: RawStore, endpoint: str, unit: dict[str, object], body: object, at: datetime
) -> None:
    raw.write(
        endpoint=endpoint,
        url=f"https://test/{endpoint}",
        params={},
        unit=unit,
        status=200,
        body=json.dumps(body),
        fetched_at=at,
    )


def _raw_fixture(root: Path) -> RawStore:
    raw = RawStore(root)
    raw.write_manifest(
        "test-collection",
        {
            "name": "test-collection",
            "created_at": "2026-09-01T00:00:00+00:00",
            "definition": {"seed": 1},
            "strata": {},
            "listed": {},
            "markets": [
                {"id": "42", "condition_id": COND, "stratum": "sports"},
                {"id": "43", "condition_id": "0xn0min", "stratum": "sports"},
            ],
        },
    )
    market = {
        "id": "42",
        "conditionId": COND,
        "questionID": "0xq",
        "slug": "m",
        "question": "Will it?",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": f'["{YES}", "{NO}"]',
        "outcomePrices": '["1", "0"]',
        "orderPriceMinTickSize": 0.001,
        "orderMinSize": 5,
        "negRisk": False,
        "feesEnabled": True,
        "feeType": "sports_fees_v2",
        "feeSchedule": {"exponent": 1, "rate": 0.03, "takerOnly": True, "rebateRate": 0.25},
        "createdAt": "2026-05-30T00:00:00Z",
        "acceptingOrdersTimestamp": "2026-05-31T23:00:00Z",
        "endDate": "2026-06-02T00:00:00Z",
        "closedTime": "2026-06-01 12:00:00+00",
        "volumeNum": 1234.567,
        "events": [{"id": "7", "slug": "e", "title": "Event"}],
    }
    _write(raw, pm.GAMMA_MARKET, {"kind": "market", "id": "42"}, market, FETCH_1)
    # A market whose Gamma object has no orderMinSize key at all (seen live): kept, min size NULL.
    no_min = {**market, "id": "43", "conditionId": "0xn0min", "clobTokenIds": '["333", "444"]'}
    del no_min["orderMinSize"]
    _write(raw, pm.GAMMA_MARKET, {"kind": "market", "id": "43"}, no_min, FETCH_1)
    _write(
        raw,
        pm.GAMMA_EVENT,
        {"kind": "event", "id": "7"},
        {"id": "7", "slug": "e", "title": "Event", "tags": [{"slug": "sports"}, {"slug": "nba"}]},
        FETCH_1,
    )
    _write(
        raw,
        pm.DATA_V2_RESOLUTIONS,
        {"kind": "resolutions", "conditions": [COND]},
        {
            "data": [
                {
                    "condition_id": COND,
                    "status": "resolved",
                    "price": "1000000000000000000",
                    "last_update_timestamp": str(T0 + 13 * 3600),
                }
            ]
        },
        FETCH_1,
    )

    def trade(ts: int, tok: str, price: float, tx: str) -> dict[str, object]:
        return {
            "transaction_hash": tx,
            "token_id": tok,
            "condition_id": COND,
            "proxy_wallet": "0xw",
            "side": "BUY",
            "price": price,
            "size": 10.0,
            "timestamp": ts,
        }

    page = {
        "data": [trade(T0 + 65, YES, 0.401, "0x1"), trade(T0 + 190, NO, 0.6, "0x2")],
        "pagination": {"next_cursor": None},
    }
    _write(raw, pm.DATA_V2_TRADES, {"kind": "trades", "condition": COND, "page": 0}, page, FETCH_1)
    _write(
        raw, pm.DATA_V2_TRADES, {"kind": "trades", "condition": COND, "page": 0}, page, FETCH_2
    )  # refetch

    # YES: minutes 0..4, sampled a few seconds past the minute; a later fetch revises minute 4.
    _write(
        raw,
        pm.CLOB_PRICES_HISTORY,
        {"kind": "prices", "token": YES, "start": T0, "end": T0 + 300},
        {"history": [{"t": T0 + 60 * i + 4, "p": 0.40 + i / 1000} for i in range(5)]},
        FETCH_1,
    )
    _write(
        raw,
        pm.CLOB_PRICES_HISTORY,
        {"kind": "prices", "token": YES, "start": T0 + 240, "end": T0 + 360},
        {"history": [{"t": T0 + 244, "p": 0.45}, {"t": T0 + 304, "p": 0.46}]},
        FETCH_2,
    )
    _write(
        raw,
        pm.CLOB_PRICES_HISTORY,
        {"kind": "prices", "token": NO, "start": T0, "end": T0 + 300},
        {"history": [{"t": T0 + 60 * i + 5, "p": 0.6} for i in range(5)]},
        FETCH_1,
    )
    return raw


def test_rebuild_is_bit_identical_and_correct(tmp_path: Path, db_url: str) -> None:
    _raw_fixture(tmp_path / "raw")
    settings = IngestSettings(data_dir=tmp_path, database_url=db_url)

    first = rebuild(settings)
    second = rebuild(settings)
    assert first.content_hash == second.content_hash
    assert first.dataset_version_id != second.dataset_version_id
    current, recorded = verify(settings)
    assert current == recorded == second.content_hash

    with psycopg.connect(psycopg_url(db_url)) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        m = conn.execute("""SELECT resolution, resolved_at, category, fee_type, fee_rate, fee_exponent,
                                   open_ts, tick_size, min_order_size, event_id FROM markets
                               ORDER BY condition_id""").fetchall()
        assert m == [
            (
                "YES",
                datetime.fromtimestamp(T0 + 13 * 3600, UTC),
                "sports",
                "sports_fees_v2",
                Decimal("0.030000"),
                1,
                datetime(2026, 5, 31, 23, tzinfo=UTC),
                Decimal("0.001000"),
                Decimal("5.000000"),
                "7",
            ),
            (
                "UNRESOLVED",  # no /v2/resolutions row: never settled on Gamma alone
                None,
                "sports",
                "sports_fees_v2",
                Decimal("0.030000"),
                1,
                datetime(2026, 5, 31, 23, tzinfo=UTC),
                Decimal("0.001000"),
                None,  # orderMinSize absent upstream: kept, not dropped, not invented
                "7",
            ),
        ]
        assert conn.execute("SELECT tags FROM events").fetchone() == (["nba", "sports"],)
        assert conn.execute("SELECT count(*) FROM trade_prints").fetchone() == (
            2,
        )  # refetch deduped

        bars = conn.execute(
            """SELECT extract(epoch FROM ts)::int - %s, price::float, is_stale, staleness_minutes
                               FROM price_bars WHERE clob_token_id = %s ORDER BY ts""",
            (T0, YES),
        ).fetchall()
        assert bars == [
            (0, 0.400, True, None),  # before the first print
            (60, 0.401, False, 0),  # print at T0+65 falls in this minute
            (120, 0.402, True, 1),
            (180, 0.403, True, 2),
            (240, 0.450, True, 3),  # newer fetch replaced 0.404
            (300, 0.460, True, 4),
        ]
        fee_types = {r[0] for r in conn.execute("SELECT fee_type FROM fee_schedules").fetchall()}
        assert "sports_fees_v2" in fee_types
        q = conn.execute(
            "SELECT quality FROM dataset_versions ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
        assert q is not None
        quality = q[0]
        assert quality["build"]["rows_rejected"]["trade_prints:duplicate"] == 2
        assert quality["totals"]["bars"] == 11
        assert quality["fees"]["fee_types_without_schedule"] == []
        assert quality["markets_without_min_order_size"] == 1
        assert quality["build"]["markets_dropped"] == []
