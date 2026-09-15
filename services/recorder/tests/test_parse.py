import json
from datetime import UTC, datetime

from syntha_recorder.parse import events, rows

NOW = datetime(2026, 9, 14, tzinfo=UTC)
WATCHED = {"111"}

# Shapes copied from live frames captured 2026-09-14.
BOOK = {
    "market": "0xm",
    "asset_id": "111",
    "timestamp": "1789350062838",
    "hash": "0c13",
    "bids": [{"price": "0.01", "size": "1125568.17"}, {"price": "0.02", "size": "148975.43"}],
    "asks": [{"price": "0.99", "size": "1274788.11"}, {"price": "0.98", "size": "100880.32"}],
    "tick_size": "0.01",
    "event_type": "book",
    "last_trade_price": "0.790",
}
PRICE_CHANGE = {
    "market": "0xm",
    "event_type": "price_change",
    "timestamp": "1789350070548",
    "price_changes": [
        {
            "asset_id": "999",
            "price": "0.8",
            "size": "90965.07",
            "side": "SELL",
            "best_bid": "0.79",
            "best_ask": "0.8",
        },
        {
            "asset_id": "111",
            "price": "0.2",
            "size": "0",
            "side": "BUY",
            "best_bid": "0.2",
            "best_ask": "0.21",
        },
    ],
}


# Live capture 2026-09-14 (v3 E7): the only per-fill fee record Polymarket publishes.
LAST_TRADE = {
    "market": "0xm",
    "asset_id": "111",
    "price": "0.002",
    "size": "5",
    "fee_rate_bps": "0",
    "side": "BUY",
    "timestamp": "1789424816252",
    "event_type": "last_trade_price",
    "transaction_hash": "0x7ec2",
}


def test_last_trade_price_becomes_a_trade_row_with_its_fee_rate() -> None:
    snaps, deltas = rows(LAST_TRADE, NOW, WATCHED)
    assert snaps == []
    (d,) = deltas
    assert d.event_type == "trade" and d.fee_rate_bps == "0"
    assert (d.side, d.price, d.size, d.best_bid) == ("BUY", "0.002", "5", None)
    assert d.ts == datetime.fromtimestamp(1789424816.252, UTC)
    assert rows({**LAST_TRADE, "asset_id": "999"}, NOW, WATCHED) == ([], [])
    # ordinary level deltas keep the default type
    _, price_deltas = rows(PRICE_CHANGE, NOW, WATCHED)
    assert price_deltas[0].event_type == "price_change" and price_deltas[0].fee_rate_bps is None


def test_initial_snapshot_arrives_as_array_later_frames_as_objects() -> None:
    assert len(events(json.dumps([BOOK, BOOK]))) == 2
    assert len(events(json.dumps(PRICE_CHANGE))) == 1
    assert events("PONG") == []
    assert events("not json") == []


def test_book_is_resorted_to_prd_convention() -> None:
    snaps, deltas = rows(BOOK, NOW, WATCHED)
    assert deltas == []
    (s,) = snaps
    assert s.bids == [["0.02", "148975.43"], ["0.01", "1125568.17"]]  # descending
    assert s.asks == [["0.98", "100880.32"], ["0.99", "1274788.11"]]  # ascending
    assert s.ts == datetime.fromtimestamp(1789350062.838, UTC)
    assert s.condition_id == "0xm"


def test_price_change_keeps_only_watched_tokens_and_zero_size_removals() -> None:
    snaps, deltas = rows(PRICE_CHANGE, NOW, WATCHED)
    assert snaps == []
    (d,) = deltas
    assert (d.clob_token_id, d.side, d.price, d.size, d.best_bid, d.best_ask) == (
        "111",
        "BUY",
        "0.2",
        "0",
        "0.2",
        "0.21",
    )


def test_unwatched_and_unknown_events_are_ignored() -> None:
    assert rows({**BOOK, "asset_id": "999"}, NOW, WATCHED) == ([], [])
    assert rows({"event_type": "new_market", "market": "0xz"}, NOW, WATCHED) == ([], [])
