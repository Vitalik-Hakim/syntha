"""Seeded, stratified market sampling that never looks at outcome-dependent fields.

Selecting markets by final volume or liquidity bakes survivorship bias into the dataset
(PRD §5.1 rule 3). The sampler sees only the market id, condition id and `feeType`, all known
when the market is created, so outcome-dependent fields cannot leak in.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from syntha_ingestion.parse import fee_category


@dataclass(frozen=True)
class Candidate:
    market_id: str
    condition_id: str
    stratum: str

    @classmethod
    def from_gamma(cls, market: dict[str, Any]) -> Candidate:
        return cls(
            market_id=str(market["id"]),
            condition_id=str(market["conditionId"]),
            stratum=fee_category(market.get("feeType")),
        )


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Split `n` across strata as evenly as possible, capped by each stratum's size.

    Capacity a small stratum cannot use is redistributed to the others. Ties are broken by
    stratum name so the allocation is deterministic.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    alloc = dict.fromkeys(sizes, 0)
    remaining = min(n, sum(sizes.values()))
    open_strata = sorted(s for s, size in sizes.items() if size > 0)
    while remaining > 0 and open_strata:
        share, extra = divmod(remaining, len(open_strata))
        for i, s in enumerate(open_strata):
            want = share + (1 if i < extra else 0)
            take = min(want, sizes[s] - alloc[s])
            alloc[s] += take
            remaining -= take
        open_strata = [s for s in open_strata if alloc[s] < sizes[s]]
    return alloc


def stratified_sample(candidates: list[Candidate], n: int, seed: int) -> list[Candidate]:
    by_stratum: dict[str, list[Candidate]] = defaultdict(list)
    seen: set[str] = set()
    for c in sorted(candidates, key=lambda c: c.condition_id):
        if c.condition_id in seen:  # a market can appear in both closed and open listings
            continue
        seen.add(c.condition_id)
        by_stratum[c.stratum].append(c)

    alloc = allocate({s: len(v) for s, v in by_stratum.items()}, n)
    chosen: list[Candidate] = []
    for stratum in sorted(by_stratum):
        rng = random.Random(f"{seed}:{stratum}")
        chosen.extend(rng.sample(by_stratum[stratum], alloc[stratum]))
    return sorted(chosen, key=lambda c: c.condition_id)
