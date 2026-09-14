from syntha_ingestion.sampling import Candidate, allocate, stratified_sample


def test_allocate_even_split() -> None:
    assert allocate({"a": 100, "b": 100, "c": 100}, 30) == {"a": 10, "b": 10, "c": 10}


def test_allocate_redistributes_small_strata() -> None:
    alloc = allocate({"big": 1000, "tiny": 3, "mid": 1000}, 100)
    assert alloc["tiny"] == 3
    assert alloc["big"] + alloc["mid"] == 97
    assert abs(alloc["big"] - alloc["mid"]) <= 1


def test_allocate_caps_at_universe() -> None:
    assert allocate({"a": 2, "b": 3}, 100) == {"a": 2, "b": 3}


def _markets(n: int, fee_type: str, volume: float) -> list[dict[str, object]]:
    return [
        {
            "id": f"{fee_type}-{i}",
            "conditionId": f"0x{fee_type}{i:04d}",
            "feeType": fee_type,
            "volumeNum": volume * (i + 1),
        }
        for i in range(n)
    ]


def test_sample_is_deterministic_and_ignores_volume() -> None:
    universe = _markets(50, "sports_fees_v2", 10) + _markets(20, "politics_fees", 1000)
    a = stratified_sample([Candidate.from_gamma(m) for m in universe], 12, seed=7)
    b = stratified_sample([Candidate.from_gamma(m) for m in reversed(universe)], 12, seed=7)
    assert a == b

    # Rewriting every outcome-dependent field must not change which markets are chosen.
    reweighted = [{**m, "volumeNum": 1e9 - float(m["volumeNum"])} for m in universe]  # type: ignore[arg-type]
    c = stratified_sample([Candidate.from_gamma(m) for m in reweighted], 12, seed=7)
    assert a == c

    assert stratified_sample([Candidate.from_gamma(m) for m in universe], 12, seed=8) != a
    assert sum(1 for x in a if x.stratum == "sports") == 6


def test_sample_deduplicates_markets_listed_twice() -> None:
    universe = _markets(5, "tech_fees", 1)
    cands = [Candidate.from_gamma(m) for m in universe + universe]
    assert len(stratified_sample(cands, 100, seed=1)) == 5


def test_saved_listing_resumes_from_the_last_saved_page(tmp_path):
    import json

    from syntha_ingestion import polymarket as pm
    from syntha_ingestion.fetch import saved_listing
    from syntha_ingestion.raw_store import RawStore

    raw = RawStore(tmp_path / "raw")
    lo, hi = "2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"

    def page(n, markets, cursor, status=200, closed=True, after=None):
        raw.write(
            endpoint=pm.GAMMA_MARKETS_KEYSET,
            url="u",
            params={"after_cursor": after},
            unit={
                "kind": "listing",
                "end_date_min": lo,
                "end_date_max": hi,
                "closed": closed,
                "page": n,
            },
            status=status,
            body=json.dumps({"markets": markets, "next_cursor": cursor}),
        )

    assert saved_listing(raw, lo, hi, True) == ([], None, 0, False, None)
    page(0, [{"conditionId": "a"}], "c1")
    page(1, [{"conditionId": "b"}], "c2", after="c1")
    page(2, [], None, status=403, after="c2")  # the cut-off page is not a saved page
    markets, cursor, nxt, complete, prev = saved_listing(raw, lo, hi, True)
    assert [m["conditionId"] for m in markets] == ["a", "b"]
    assert (cursor, nxt, complete, prev) == ("c2", 2, False, "c1")
    page(1, [{"conditionId": "b2"}], "c2b", after="c1")  # a newer attempt's page 1 wins
    markets, cursor, nxt, complete, prev = saved_listing(raw, lo, hi, True)
    assert [m["conditionId"] for m in markets] == ["a", "b2"] and (cursor, prev) == ("c2b", "c1")
    page(2, [{"conditionId": "c"}], None, after="c2b")  # final page: complete, no continuation
    markets, cursor, nxt, complete, prev = saved_listing(raw, lo, hi, True)
    assert [m["conditionId"] for m in markets] == ["a", "b2", "c"]
    assert (cursor, nxt, complete, prev) == (None, 3, True, None)
    assert saved_listing(raw, lo, hi, False) == ([], None, 0, False, None)
