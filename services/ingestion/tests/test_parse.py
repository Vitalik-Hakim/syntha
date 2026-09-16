from datetime import UTC, datetime

import pytest

from syntha_ingestion.parse import fee_category, parse_ts, print_key, resolve_market

YES_NO = '["Yes", "No"]'


@pytest.mark.parametrize(
    ("fee_type", "category"),
    [
        (None, "no_fee"),
        ("", "no_fee"),
        ("sports_fees_v2", "sports"),
        ("sports_fees_v3", "sports"),
        ("politics_fees", "politics"),
        ("finance_prices_fees", "finance"),
        ("crypto_15_min", "crypto"),
        ("crypto_fees", "crypto"),
        ("crypto_fees_v2", "crypto"),
        ("general_fees", "other"),
    ],
)
def test_fee_category(fee_type: str | None, category: str) -> None:
    assert fee_category(fee_type) == category


@pytest.mark.parametrize(
    "value", ["2026-04-09T00:28:21Z", "2026-04-09 00:28:21+00", "2026-04-09T00:28:21+00:00"]
)
def test_parse_ts_formats(value: str) -> None:
    assert parse_ts(value) == datetime(2026, 4, 9, 0, 28, 21, tzinfo=UTC)


def test_parse_ts_epoch_string() -> None:
    assert parse_ts("1730902492") == datetime.fromtimestamp(1730902492, UTC)


def _gamma(prices: str, outcomes: str = YES_NO) -> dict[str, str]:
    return {"outcomes": outcomes, "outcomePrices": prices}


def _v2(price: str, status: str = "resolved") -> dict[str, str]:
    return {"status": status, "price": price, "last_update_timestamp": "1775694501"}


def test_resolution_yes() -> None:
    r = resolve_market(_gamma('["1", "0"]'), _v2("1000000000000000000"))
    assert (r.resolution, r.anomaly) == ("YES", None)
    assert r.resolved_at == datetime.fromtimestamp(1775694501, UTC)


def test_resolution_no() -> None:
    assert resolve_market(_gamma('["0", "1"]'), _v2("0")).resolution == "NO"


def test_resolution_invalid_fifty_fifty() -> None:
    r = resolve_market(_gamma('["0.5", "0.5"]'), _v2("500000000000000000"))
    assert r.resolution == "INVALID"


def test_non_yes_no_outcomes_use_index_labels() -> None:
    r = resolve_market(_gamma('["0", "1"]', '["Lakers", "Celtics"]'), _v2("0"))
    assert r.resolution == "OUTCOME_1"


def test_unresolved_has_no_resolved_at() -> None:
    r = resolve_market(_gamma('["0.42", "0.58"]'), _v2("", status="proposed"))
    assert (r.resolution, r.resolved_at) == ("UNRESOLVED", None)
    assert resolve_market(_gamma('["1", "0"]'), None).resolution == "UNRESOLVED"


def test_sources_disagreeing_is_unresolved_and_reported() -> None:
    r = resolve_market(_gamma('["1", "0"]'), _v2("0"))
    assert r.resolution == "UNRESOLVED"
    assert r.resolved_at is None
    assert r.anomaly and "disagrees" in r.anomaly


def test_print_key_is_stable_and_discriminating() -> None:
    t = {
        "transaction_hash": "0xabc",
        "token_id": "1",
        "proxy_wallet": "0xw",
        "side": "BUY",
        "price": 0.42,
        "size": 10.0,
        "timestamp": 1780272007,
    }
    assert print_key(t) == print_key(dict(t))
    assert print_key(t) != print_key({**t, "size": 10.5})
    assert print_key(t) != print_key({**t, "side": "SELL"})


def _payouts(payouts: list[int]) -> dict[str, object]:
    return {
        "status": "resolved",
        "payouts": payouts,
        "resolved_at": "2026-07-08T11:15:35Z",
        "last_update_timestamp": "2026-07-08T11:15:35Z",
    }


def test_payouts_shaped_rows_are_cross_checked() -> None:
    r = resolve_market(_gamma('["0", "1"]'), _payouts([0, 1_000_000]))
    assert r.resolution == "NO"
    assert r.resolved_at == datetime(2026, 7, 8, 11, 15, 35, tzinfo=UTC)
    assert (
        resolve_market(_gamma('["0.5", "0.5"]'), _payouts([500_000, 500_000])).resolution
        == "INVALID"
    )
    mismatch = resolve_market(_gamma('["1", "0"]'), _payouts([0, 1_000_000]))
    assert mismatch.resolution == "UNRESOLVED" and mismatch.anomaly


def test_unconfirmed_by_chain_is_unresolved() -> None:
    r = resolve_market(_gamma('["1", "0"]'), {"status": "resolved", "price": None})
    assert r.resolution == "UNRESOLVED"
    assert r.anomaly and "unrecognised chain payout" in r.anomaly


def test_question_window_marks_short_crypto_markets_ultra_short() -> None:
    from datetime import UTC, datetime

    from syntha_ingestion.parse import duration_class, question_window_minutes

    assert question_window_minutes("Bitcoin Up or Down - March 28, 4:15AM-4:30AM ET") == 15
    assert question_window_minutes("Ethereum Up or Down - March 28, 2:25AM-2:30AM ET") == 5
    assert question_window_minutes("Solana Up or Down - July 4, 11PM-12AM ET") == 60
    assert question_window_minutes("Will BTC move 2% in the next 5 minutes?") == 5
    assert question_window_minutes("Will Bitcoin be above $120k on July 31?") is None
    assert (
        question_window_minutes("Bitcoin Up or Down - August 5, 12PM ET") == 60
    )  # the hourly series
    assert (
        question_window_minutes("Will Ethereum be between $3000 and $3100 on July 14 at 5PM ET?")
        is None
    )
    open_ts = datetime(2026, 3, 25, tzinfo=UTC)
    end_ts = datetime(2026, 3, 28, 8, 30, tzinfo=UTC)
    # listed three days ahead: end − open says multi_day, the question says 15 minutes
    assert duration_class(open_ts, end_ts) == "multi_day"
    assert (
        duration_class(open_ts, end_ts, "Bitcoin Up or Down - March 28, 4:15AM-4:30AM ET")
        == "ultra_short"
    )
    assert duration_class(open_ts, end_ts, "Will Bitcoin be above $120k on July 31?") == "multi_day"


def test_ultra_short_fixture_regression() -> None:
    """The M9 discovery artefact: every question form of the short crypto series listed days
    ahead must classify as ultra_short (docs/DATA_NOTES.md, 'M9 discovery'); the fixture keeps
    the real strings so a parser change cannot quietly readmit them to the primary cells."""
    import json
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from syntha_ingestion.parse import duration_class, question_window_minutes

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "ultra_short_questions.json").read_text()
    )
    end_ts = datetime(2026, 3, 28, 8, 30, tzinfo=UTC)
    open_ts = end_ts - timedelta(days=3)
    for case in fixture["cases"]:
        q = case["question"]
        assert question_window_minutes(q) == case["window_minutes"], q
        assert duration_class(open_ts, end_ts, q) == case["class_when_listed_3_days_ahead"], q
