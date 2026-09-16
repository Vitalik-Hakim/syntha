"""Calibration census (PRD v3 §4.1): the pure observation arithmetic, the sampler's caps, the
print walk's depth cap, and a bit-identical rebuild from a synthetic raw layer."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config

from syntha_engine.data import MarketTerms
from syntha_engine.execution.fees import fee_amount
from syntha_ingestion import census as C
from syntha_ingestion import polymarket as pm
from syntha_ingestion.config import IngestSettings, psycopg_url
from syntha_ingestion.http import ApiClient
from syntha_ingestion.raw_store import RawStore

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages" / "storage" / "alembic.ini"
T0 = 1_751_328_000  # 2025-07-01 00:00:00 UTC
DAY = 86_400
YES, NO = "yes-1", "no-1"


def _print(ts: int, price: float, size: float, side: int = 1, token: str = YES) -> C.Print:
    return C.Print(ts, price, size, side, token)


# ------------------------------------------------------------------ pure arithmetic


def test_offsets_stay_inside_the_market_life() -> None:
    labels = [lbl for lbl, _ in C.offset_times(T0, T0 + 10 * DAY, None)]
    assert labels == ["-7d", "-3d", "-24h", "-6h", "-1h", "-15m", "+1h", "+24h"]
    short = dict(C.offset_times(T0, T0 + 2 * DAY, None))
    assert set(short) == {"-24h", "-6h", "-1h", "-15m", "+1h", "+24h"}
    assert short["-24h"] == T0 + DAY and short["+24h"] == T0 + DAY
    # closed 20 hours in: +24h is after the close and is skipped
    closed = dict(C.offset_times(T0, T0 + 2 * DAY, T0 + 20 * 3600))
    assert "+24h" not in closed and "+1h" in closed


def test_mid_staleness_fill_and_liquidity_rules() -> None:
    series = [(T0 + h * 3600, 0.40) for h in range(48)]
    ts = T0 + DAY
    assert C.mid_at(series, ts) == 0.40
    assert C.mid_at(series, T0 - 1) is None  # nothing at or before
    prints = [
        _print(ts - 7200, 0.39, 10),  # two hours before: staleness 120
        _print(ts - 3600, 0.40, 10),  # inside the 24h liquidity window: $4
        _print(ts - 3000, 0.40, 10, side=-1),  # a taker sell: not ask liquidity, but a print
        _print(ts + 60, 0.40, 100),  # fills at the limit
        _print(ts + 120, 0.42, 100),  # above mid + tick: could not have filled
        _print(ts + 700, 0.41, 50),  # only in the 30-minute window
        _print(ts + 60, 0.30, 999, token=NO),  # the other token
    ]
    prints.sort(key=lambda p: p.ts)
    yes = [p for p in prints if p.token == YES]
    assert C.staleness_minutes([p.ts for p in yes], ts) == 50  # the sell 50 min before counts
    vwap10, size10, first10 = C.fill_in_window(yes, ts, 600, 0.41)
    assert (vwap10, size10, first10) == (0.40, 100.0, 0.40)
    vwap30, size30, first30 = C.fill_in_window(yes, ts, 1800, 0.41)
    assert vwap30 == pytest.approx((40 + 20.5) / 150) and size30 == 150.0 and first30 == 0.40
    assert C.fill_in_window(yes, ts, 600, 0.35) == (None, 0.0, None)  # nothing at the limit
    complete = C.PrintWalk(yes, complete=True, truncated=False)
    assert C.liquidity_24h(yes, ts, complete) == pytest.approx(4.0 + 3.9)
    partial = C.PrintWalk(yes, complete=False, truncated=True)
    assert C.liquidity_24h(yes, ts, partial) is None  # the window was not fully fetched
    later = C.PrintWalk(yes, complete=False, truncated=False)
    assert C.liquidity_24h(yes, ts + 3 * DAY, later) == 0.0  # covered: earliest print precedes


def test_payouts_come_from_the_resolution_only() -> None:
    assert C.payout_for("YES", 0) == 1 and C.payout_for("YES", 1) == 0
    assert C.payout_for("NO", 1) == 1 and C.payout_for("INVALID", 0) == Decimal("0.5")
    assert C.payout_for("OUTCOME_2", 2) == 1 and C.payout_for("UNRESOLVED", 0) is None


def test_observation_rows_by_hand() -> None:
    end = T0 + 2 * DAY
    ts = T0 + DAY  # the -24h offset
    prints = sorted(
        [
            _print(ts - 3600, 0.40, 10),
            _print(ts + 60, 0.40, 100),
            _print(ts + 120, 0.42, 100),
            _print(ts + 700, 0.41, 50),
        ],
        key=lambda p: p.ts,
    )
    terms = MarketTerms(
        "c1", "e1", "sports", Decimal("0.01"), Decimal("5"), T0, T0, end, None, None, None, None
    )
    inputs = C.MarketInputs(
        condition_id="c1",
        event_id="e1",
        tokens=(YES, NO),
        category="sports",
        duration="multi_day",
        structure="binary",
        deadline="ON",
        neg_risk=False,
        open_s=T0,
        end_s=end,
        close_s=None,
        terms=terms,
        resolution="YES",
        walk=C.PrintWalk(prints, complete=True, truncated=False),
        series={YES: [(T0 + h * 3600, 0.40) for h in range(49)], NO: []},
    )
    schedules = C.fee_entries()
    rows = C.observations(inputs, schedules, "census-test")
    by_key = {(r[2], r[5]): r for r in rows}
    assert len(rows) == 12  # 6 offsets × 2 tokens
    assert by_key[(YES, "-24h")][16] == "0.400000"  # first qualifying print
    row = by_key[(YES, "-24h")][:16]
    (
        _cid,
        cond,
        tok,
        ev,
        idx,
        label,
        when,
        mid,
        stale,
        f10,
        f30,
        size10,
        liq,
        fee_h,
        fee_c,
        pay,
    ) = row
    assert (cond, tok, ev, idx, label) == ("c1", YES, "e1", 0, "-24h")
    assert when == "2025-07-02 00:00:00.000000+00" and mid == "0.400000"
    assert stale == 60 and f10 == "0.400000" and size10 == "100.000000"
    assert Decimal(f30) == Decimal("0.403333") and liq == "4.00"
    assert fee_h == "0.000000"  # 2025: fee-free
    current = C.current_fee_terms("sports", schedules)
    assert (
        current is not None and fee_c == f"{fee_amount(Decimal(1), Decimal('0.40'), current):.6f}"
    )
    assert pay == "1.000000"
    no_row = by_key[(NO, "-24h")]
    assert no_row[7] is None and no_row[9] is None and no_row[15] == "0.000000"  # no NO series


def test_sampler_caps_each_stratum_and_is_seeded() -> None:
    def market(i: int, minutes: int, fee: str | None = "sports_fees_v3") -> dict:
        return {
            "id": str(i),
            "conditionId": f"0x{i:03d}",
            "clobTokenIds": json.dumps([f"y{i}", f"n{i}"]),
            "feeType": fee,
            "acceptingOrdersTimestamp": "2025-07-01T00:00:00Z",
            "endDate": datetime.fromtimestamp(T0 + minutes * 60, UTC).isoformat(),
            "events": [{"id": f"ev{i % 3}"}],
        }

    event = {"tags": [{"slug": "crypto"}], "markets": [{}, {}, {}]}
    frame = [C.classify(market(i, 30), None) for i in range(20)]  # ultra_short sports
    frame += [C.classify(market(100 + i, 3 * 24 * 60, None), event) for i in range(20)]
    frame_ok = [f for f in frame if f is not None]
    assert {f.stratum for f in frame_ok} == {"sports/ultra_short", "crypto/multi_day"}
    assert all(f.structure == "sibling" for f in frame_ok if f.category == "crypto")
    chosen = C.allocate(frame_ok, seed=1, per_stratum=5, ultra_short_cap=2)
    counts: dict[str, int] = {}
    for f in chosen:
        counts[f.stratum] = counts.get(f.stratum, 0) + 1
    assert counts == {"sports/ultra_short": 2, "crypto/multi_day": 5}
    assert chosen == C.allocate(frame_ok, seed=1, per_stratum=5, ultra_short_cap=2)
    assert chosen != C.allocate(frame_ok, seed=2, per_stratum=5, ultra_short_cap=2)
    assert C.classify({**market(1, 30), "clobTokenIds": "[]"}, None) is None


# ------------------------------------------------------------------ the print walk


def _client(tmp_path: Path, handler: object) -> ApiClient:
    async def no_sleep(_s: float) -> None:
        return None

    return ApiClient(
        RawStore(tmp_path / "raw"),
        requests_per_second=1000,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        sleep=no_sleep,
    )


def _trade(ts: int, i: int) -> dict:
    return {
        "transaction_hash": f"0x{i}",
        "token_id": YES,
        "condition_id": "0xc",
        "proxy_wallet": "0xw",
        "side": "BUY",
        "price": "0.4",
        "size": "1",
        "timestamp": ts,
    }


def test_print_walk_stops_at_depth_or_page_cap(tmp_path: Path) -> None:
    end = T0 + 30 * DAY
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        cursor = q.get("cursor", [None])[0]
        calls.append(cursor)
        page = len(calls) - 1
        # each page goes one day further back, newest first; never ends by itself
        rows = [_trade(end - page * DAY - k, page * 10 + k) for k in range(3)]
        return httpx.Response(
            200, json={"data": rows, "pagination": {"has_more": True, "next_cursor": f"c{page}"}}
        )

    async def run() -> tuple[dict, dict]:
        async with _client(tmp_path, handler) as api:
            deep = await pm.walk_trades_window(
                api, "0xc", stop_before_ts=end - 8 * DAY, max_pages=10
            )
            calls.clear()
            capped = await pm.walk_trades_window(
                api, "0xc", stop_before_ts=end - 30 * DAY, max_pages=4
            )
            return deep, capped

    deep, capped = asyncio.run(run())
    # page 8 reaches day 8 (< end − 8d) after nine pages: depth reached, nothing truncated
    assert deep["pages"] == 9 and deep["truncated"] is False
    assert capped["pages"] == 4 and capped["truncated"] is True and capped["prints"] == 12
    records = [r for r in RawStore(tmp_path / "raw").records(pm.DATA_V2_TRADES)]
    walk = C.print_walk(records[deep["pages"] :], end)  # the capped walk alone
    assert walk.truncated and not walk.complete and len(walk.prints) == 12
    assert walk.covers(end - 3 * DAY) and not walk.covers(end - 5 * DAY)


# ------------------------------------------------------------------ bit-identical rebuild


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
    raw: RawStore, endpoint: str, unit: dict, body: object, params: dict | None = None
) -> None:
    raw.write(
        endpoint=endpoint,
        url=f"https://test/{endpoint}",
        params=params or {},
        unit=unit,
        status=200,
        body=json.dumps(body),
        fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
    )


def test_census_build_is_bit_identical(tmp_path: Path, db_url: str) -> None:
    raw = RawStore(tmp_path / "raw")
    end_date_min, end_date_max = C.month_window("2025-07")
    end = T0 + 2 * DAY
    listing_unit = {
        "kind": "listing",
        "end_date_min": end_date_min,
        "end_date_max": end_date_max,
        "closed": True,
        "page": 0,
    }
    market = {
        "id": "1",
        "conditionId": "0xc",
        "question": "Will it rain in Paris on July 3?",
        "clobTokenIds": json.dumps([YES, NO]),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["1", "0"]),
        "orderPriceMinTickSize": "0.01",
        "orderMinSize": "5",
        "acceptingOrdersTimestamp": datetime.fromtimestamp(T0, UTC).isoformat(),
        "endDate": datetime.fromtimestamp(end, UTC).isoformat(),
        "closedTime": datetime.fromtimestamp(end + 600, UTC).isoformat(),
        "createdAt": datetime.fromtimestamp(T0 - DAY, UTC).isoformat(),
        "negRisk": False,
        "feeType": None,
        "feesEnabled": False,
        "events": [{"id": "ev1"}],
        "umaResolutionStatus": "resolved",
    }
    _write(raw, pm.GAMMA_MARKETS_KEYSET, listing_unit, {"markets": [market], "next_cursor": None})
    _write(
        raw,
        pm.GAMMA_EVENT,
        {"kind": "event", "id": "ev1"},
        {"id": "ev1", "tags": [{"slug": "weather"}], "markets": [{}]},
    )
    _write(
        raw,
        pm.DATA_V2_RESOLUTIONS,
        {"kind": "resolutions", "conditions": ["0xc"]},
        {
            "data": [
                {
                    "condition_id": "0xc",
                    "status": "resolved",
                    "price": str(10**18),
                    "last_update_timestamp": "2025-07-03T02:00:00Z",
                }
            ]
        },
    )
    ts = T0 + DAY
    _write(
        raw,
        pm.DATA_V2_TRADES,
        {"kind": "trades", "condition": "0xc", "page": 0},
        {
            "data": [_trade(ts + 60, 1), _trade(ts - 3600, 2)],
            "pagination": {"has_more": False, "next_cursor": None},
        },
    )
    _write(
        raw,
        pm.CLOB_PRICES_HISTORY,
        {"kind": "prices", "token": YES, "start": T0, "end": end, "fidelity": 60},
        {"history": [{"t": T0 + h * 3600, "p": 0.4} for h in range(49)]},
    )
    manifest = {
        "name": "census-2025-07",
        "month": "2025-07",
        "created_at": "2026-09-15T00:00:00+00:00",
        "definition": {"seed": 1},
        "frame": {},
        "markets": [
            {
                "id": "1",
                "condition_id": "0xc",
                "event_id": "ev1",
                "stratum": "weather/multi_day",
                "structure": "binary",
                "tokens": [YES, NO],
            }
        ],
    }
    path = C.manifest_path(raw, "census-2025-07")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))

    settings = IngestSettings(data_dir=tmp_path, database_url=db_url)
    first = C.build_census(settings, "2025-07")
    second = C.build_census(settings, "2025-07")
    assert first.content_hash == second.content_hash
    assert first.n_markets == 1 and first.n_observations == 12
    assert C.verify_census(settings, "2025-07") == (first.content_hash, first.content_hash)
    with psycopg.connect(psycopg_url(db_url)) as conn:
        rows = conn.execute(
            "SELECT offset_label, mid, fill_10m, liq_24h, payout, staleness_minutes FROM census_observations "
            "WHERE census_id = 'census-2025-07' AND clob_token_id = %s ORDER BY offset_label",
            (YES,),
        ).fetchall()
        cm = conn.execute(
            "SELECT category, duration_class, structure, deadline_type, resolution, prints_truncated "
            "FROM census_markets WHERE census_id = 'census-2025-07'"
        ).fetchone()
    by_label = {r[0]: r for r in rows}
    assert by_label["-24h"][1:] == (
        Decimal("0.400000"),
        Decimal("0.400000"),
        Decimal("0.40"),
        Decimal("1.000000"),
        60,
    )
    assert cm == ("weather", "multi_day", "binary", "ON", "YES", False)


def test_window_vwap_selects_dips_and_the_first_fill_does_not() -> None:
    """Regression fixture for the second M9 artefact (docs/DATA_NOTES.md, 'M9 discovery, first
    pass'): the fill VWAP averages only the prints at or below mid + 1 tick, so a market that
    dips inside the window "fills" below mid on *both* tokens — an impossible pair whose returns
    sum to more than zero mechanically. `fill_first_10m` (the first qualifying print) does not
    have this property; the census carries both and S1 reports both."""
    ts = T0 + DAY
    yes_prints = [
        _print(ts + 60, 0.50, 100),
        _print(ts + 120, 0.44, 100),
        _print(ts + 180, 0.50, 100),
    ]
    no_prints = [
        _print(ts + 60, 0.50, 100, token=NO),
        _print(ts + 240, 0.44, 100, token=NO),
        _print(ts + 300, 0.50, 100, token=NO),
    ]
    mid_yes = mid_no = 0.50
    vwap_yes, _, first_yes = C.fill_in_window(yes_prints, ts, 600, mid_yes + 0.01)
    vwap_no, _, first_no = C.fill_in_window(no_prints, ts, 600, mid_no + 0.01)
    assert vwap_yes == pytest.approx(0.48) and vwap_no == pytest.approx(0.48)
    assert vwap_yes + vwap_no < 1.0  # both "filled" below fair: the artefact
    assert first_yes == 0.50 and first_no == 0.50
    assert first_yes + first_no == pytest.approx(1.0)  # the first-print fill has no such gain
    # the mechanical gain: with payout 1 to one token and 0 to the other, the VWAP pair returns
    # (1 - 0.48)/0.48 + (0 - 0.48)/0.48 = +8.3% for free; the first-print pair returns 0.
    gain_vwap = (1 - vwap_yes) / vwap_yes + (0 - vwap_no) / vwap_no
    gain_first = (1 - first_yes) / first_yes + (0 - first_no) / first_no
    assert gain_vwap == pytest.approx(0.0833, abs=1e-3) and gain_first == pytest.approx(0.0)
