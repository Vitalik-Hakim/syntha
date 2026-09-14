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
