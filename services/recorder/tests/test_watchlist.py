"""The designed watchlist (v3 E7): classification from Gamma rows and the pure refill plan."""

from syntha_recorder.watchlist import Candidate, category_of, classify, plan, tier_of


def _gamma(cid: str, fee: str | None, neg_risk: bool, volume: float, **kw: object) -> dict:
    row = {
        "conditionId": cid,
        "feeType": fee,
        "negRisk": neg_risk,
        "volume24hr": volume,
        "clobTokenIds": f'["{cid}-y", "{cid}-n"]',
        "enableOrderBook": True,
        "acceptingOrders": True,
        "closed": False,
        "events": [{"id": f"ev-{cid}", "negRisk": neg_risk}],
    }
    row.update(kw)
    return row


def test_category_and_tier_rules() -> None:
    assert category_of("sports_fees_v3") == "sports"
    assert category_of("economics_fees") == "economics"
    assert category_of(None) == "no_fee"
    assert tier_of(50_000) == "liquid" and tier_of(500) == "thin"
    assert tier_of(5_000) is None and tier_of(10) is None  # middle band and dead markets


def test_classify_reads_the_verified_gamma_fields() -> None:
    c = classify(_gamma("c1", "politics_fees", True, 25_000))
    assert c is not None
    assert (c.stratum, c.event_id, c.tokens, c.open) == (
        "politics/multi/liquid",
        "ev-c1",
        ("c1-y", "c1-n"),
        True,
    )
    assert classify(_gamma("c2", "sports_fees_v3", False, 1, enableOrderBook=False)) is None
    closed = classify(_gamma("c3", "sports_fees_v3", False, 1, closed=True))
    assert closed is not None and not closed.open


def test_plan_retires_closed_markets_and_fills_each_stratum_from_the_most_liquid() -> None:
    active = {"old": "sports/binary/liquid", "gone": "sports/binary/thin"}
    candidates = [
        c
        for c in (
            classify(_gamma("s1", "sports_fees_v3", False, 90_000)),
            classify(_gamma("s2", "sports_fees_v3", False, 40_000)),
            classify(_gamma("s3", "sports_fees_v3", False, 30_000)),
            classify(_gamma("t1", "sports_fees_v3", False, 800)),
            classify(_gamma("mid", "sports_fees_v3", False, 5_000)),  # neither tier
            classify(_gamma("w1", "weather_fees", False, 90_000)),  # not a chosen category
            classify(_gamma("old", "sports_fees_v3", False, 90_000)),  # already watched
        )
        if c is not None
    ]
    p = plan(active, candidates, {"old": True, "gone": False}, target=2, categories=("sports",))
    assert p.remove == ["gone"]
    # liquid binary sports: 'old' stays (1 kept) so one more, the most liquid new one
    assert [c.condition_id for c in p.add if c.stratum == "sports/binary/liquid"] == ["s1"]
    # thin binary sports: 'gone' retired, so both slots are open but only one thin candidate exists
    assert [c.condition_id for c in p.add if c.stratum == "sports/binary/thin"] == ["t1"]
    assert all(isinstance(c, Candidate) for c in p.add)
    assert not [c for c in p.add if c.condition_id in ("mid", "w1", "old")]


def test_plan_fills_the_final_24h_politics_slot_from_markets_ending_soon() -> None:
    from datetime import UTC, datetime, timedelta

    from syntha_recorder.watchlist import FINAL_24H_STRATUM, FinalSlot

    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    soon = (now + timedelta(hours=6)).isoformat()
    later = (now + timedelta(hours=40)).isoformat()
    rows = [
        _gamma("p1", "politics_fees", False, 5_000) | {"endDate": soon},  # mid tier, ends soon
        _gamma("p2", "politics_fees", False, 300) | {"endDate": soon},  # thin, ends soon
        _gamma("p3", "politics_fees", False, 90_000) | {"endDate": later},  # not in 24h
        _gamma("p4", "politics_fees", False, 50) | {"endDate": soon},  # no volume to record
        _gamma("s1", "sports_fees_v3", False, 90_000) | {"endDate": soon},  # wrong category
    ]
    candidates = [c for c in (classify(r) for r in rows) if c is not None]
    p = plan({}, candidates, {}, target=0, categories=("politics",), final_24h_slots=1, now=now)
    assert [c.condition_id for c in p.add] == ["p1"]  # the most liquid one that ends in time
    assert isinstance(p.add[0], FinalSlot) and p.add[0].stratum == FINAL_24H_STRATUM
    p2 = plan({}, candidates, {}, target=0, categories=("politics",), final_24h_slots=5, now=now)
    assert [c.condition_id for c in p2.add] == ["p1", "p2"]
    # a market already watched through the slot is not re-added while it is active
    p3 = plan(
        {"p1": FINAL_24H_STRATUM},
        candidates,
        {"p1": True},
        target=0,
        categories=("politics",),
        final_24h_slots=1,
        now=now,
    )
    assert p3.add == []
