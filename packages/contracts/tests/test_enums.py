from syntha_contracts import ExitReason, FillStatus, Side


def test_exit_reasons_match_prd_12_4() -> None:
    assert {r.value for r in ExitReason} == {
        "TAKE_PROFIT",
        "STOP_LOSS",
        "MAX_HOLD",
        "PRICE_TARGET",
        "RULE_EXIT",
        "MARKET_RESOLVED",
        "END_OF_BACKTEST",
        "FORCED_EXPOSURE_LIMIT",
        "DATA_GAP",
    }


def test_fill_statuses_match_prd_7() -> None:
    assert {s.value for s in FillStatus} == {
        "FILLED",
        "PARTIAL",
        "UNEXECUTABLE",
        "REJECTED_MIN_SIZE",
    }


def test_no_short_side() -> None:
    # PRD §3.3: every side buys a token; EITHER lets the rules pick which. Nothing sells short.
    assert {s.value for s in Side} == {"BUY_YES", "BUY_NO", "EITHER"}
    assert not any("SELL" in s.value or "SHORT" in s.value for s in Side)
