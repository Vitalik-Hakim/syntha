"""Near-miss selection (PRD §13.2): which non-firing bars to keep a trace for.

Three kinds, so the user can ask "why didn't it trade here?":
- RULES_FAILED: the market was eligible but the entry rules did not hold. Kept: the bars nearest
  to firing (lowest closeness score), spread at least `separation` bars apart so one slow drift
  past a threshold doesn't fill every slot.
- BLOCKED_BY_UNIVERSE: the entry rules held but a universe filter (as-of volume, time to the
  scheduled end, staleness, market open) blocked the bar. Kept: the first bar of evenly spaced
  blocked stretches.
- BLOCKED_BY_CONSTRAINT: the rules and filters held but a portfolio constraint (max open
  positions, one position per event, cooldown, re-entry) blocked the signal. Kept: the first bar
  of the first blocked stretches.

Selection is capped per market and per run and is deterministic: ties go to the earlier bar.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from syntha_engine.data import FloatArray, IntArray

RULES_FAILED = "RULES_FAILED"
BLOCKED_BY_UNIVERSE = "BLOCKED_BY_UNIVERSE"
BLOCKED_BY_CONSTRAINT = "BLOCKED_BY_CONSTRAINT"
KINDS = (RULES_FAILED, BLOCKED_BY_UNIVERSE, BLOCKED_BY_CONSTRAINT)

PER_MARKET = {RULES_FAILED: 3, BLOCKED_BY_UNIVERSE: 2, BLOCKED_BY_CONSTRAINT: 2}
PER_RUN = {RULES_FAILED: 3000, BLOCKED_BY_UNIVERSE: 1000, BLOCKED_BY_CONSTRAINT: 1000}

_INDEX_BITS = 26  # bars per token addressable in a sort key (~127 years of 1-minute bars)
_SCORE_QUANTUM = 1e-6


def nearest(score: FloatArray, candidates: IntArray, n: int, separation: int) -> list[int]:
    """Up to `n` of `candidates` (local bar indices) with the lowest score, pairwise at least
    `separation` bars apart. Ties go to the earlier bar."""
    if n <= 0 or candidates.size == 0:
        return []
    # One integer key per bar (quantised score, then index) makes the partial sort exact and
    # independent of how argpartition orders equal values.
    q = np.round(score[candidates] / _SCORE_QUANTUM).astype(np.int64)
    keys = (q << _INDEX_BITS) | candidates.astype(np.int64)
    pool = min(keys.size, max(64 * n, 256))
    if pool < keys.size:
        keys = keys[np.argpartition(keys, pool - 1)[:pool]]
    chosen: list[int] = []
    for key in np.sort(keys):
        i = int(key & ((1 << _INDEX_BITS) - 1))
        if all(abs(i - c) >= separation for c in chosen):
            chosen.append(i)
            if len(chosen) == n:
                break
    return sorted(chosen)


def stretch_starts(mask: np.ndarray, n: int) -> list[int]:
    """First bars of up to `n` evenly spaced runs of consecutive True values."""
    if n <= 0 or not mask.any():
        return []
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]])
    if starts.size <= n:
        return [int(x) for x in starts]
    picks = np.unique(np.round(np.linspace(0, starts.size - 1, n)).astype(np.int64))
    return [int(x) for x in starts[picks]]


def cap_run(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Apply the per-run caps: nearest rows for RULES_FAILED, evenly spaced in time for the
    blocked kinds. Returns the kept rows in (ts, token, kind) order and whether any were dropped."""
    kept: list[dict[str, Any]] = []
    capped = False
    for kind in KINDS:
        group = [r for r in rows if r["kind"] == kind]
        limit = PER_RUN[kind]
        if len(group) > limit:
            capped = True
            if kind == RULES_FAILED:
                group.sort(key=lambda r: (r["score"], r["ts"], r["clob_token_id"]))
                group = group[:limit]
            else:
                group.sort(key=lambda r: (r["ts"], r["clob_token_id"]))
                idx = np.unique(np.round(np.linspace(0, len(group) - 1, limit)).astype(np.int64))
                group = [group[int(i)] for i in idx]
        kept.extend(group)
    kept.sort(key=lambda r: (r["ts"], r["clob_token_id"], KINDS.index(str(r["kind"]))))
    return kept, capped
