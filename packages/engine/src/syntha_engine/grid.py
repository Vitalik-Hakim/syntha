"""The shared clock and per-token arrays aligned to it (PRD §9.2).

Bar k covers [start + k·step, start + (k+1)·step). A bar's data (its last price sample, its
prints) is known at the bar's *close*. Signals computed from bar k therefore execute no earlier
than bar k+1 unless `decision_lag_bars` is 0, which is flagged as optimistic.

Coarser bars aggregate 1-minute data: the bar price is the last sample in the bucket and prints
are summed. Coarse data is never requested from the API.

Forward-filled prices are allowed (they only use the past) and always carry staleness
(PRD §5.1 rule 5). Nothing is ever interpolated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from syntha_engine.data import FloatArray, IntArray, MarketTerms, TokenData

NAN = float("nan")


@dataclass(frozen=True, slots=True)
class Grid:
    start: int
    step: int  # seconds
    n: int

    @classmethod
    def build(cls, first_ts: int, last_ts: int, step_minutes: int) -> Grid:
        step = step_minutes * 60
        start = first_ts // step * step
        end = last_ts // step * step
        return cls(start=start, step=step, n=(end - start) // step + 1)

    def ts(self, k: int) -> int:
        return self.start + k * self.step

    def close(self, k: int) -> int:
        return self.start + (k + 1) * self.step

    def bar_of(self, ts: int) -> int:
        """Index of the bar containing `ts` (may be outside [0, n))."""
        return (ts - self.start) // self.step

    def bars_of(self, ts: IntArray) -> IntArray:
        return (ts - self.start) // self.step

    def first_bar_at_or_after(self, ts: int) -> int:
        """Smallest k with ts(k) >= ts."""
        return -((self.start - ts) // self.step)


@dataclass(frozen=True, slots=True)
class TokenFrame:
    """One token's data on the grid, local range [k0, k0 + n).

    Prices are dense (one value per bar). Prints are sparse: real markets print in well under 1%
    of minutes, so per-bar print aggregates are stored only for bars that have prints, and dense
    views are materialized on demand by the features that need them.
    """

    token: TokenData
    terms: MarketTerms
    grid: Grid
    k0: int
    n: int
    observed: np.ndarray  # bool: a price sample fell in this bar
    price: FloatArray  # forward-filled; NaN before the first sample
    last_obs: npt.NDArray[
        np.int32
    ]  # local index of the latest sample at or before each bar, -1 if none
    pr_local: IntArray  # local bar indices that have prints (ascending, unique)
    pr_volume: FloatArray
    pr_count: FloatArray
    pr_notional: FloatArray
    pr_min: FloatArray
    pr_max: FloatArray
    prior_notional: float  # traded notional before the frame (as-of volume)

    @property
    def token_id(self) -> str:
        return self.token.clob_token_id

    @property
    def n_prints(self) -> int:
        return int(self.pr_count.sum())

    def price_at(self, k: int) -> float:
        """Latest known price at bar k; carries forward past the end of the data."""
        i = min(k - self.k0, self.n - 1)
        return NAN if i < 0 else float(self.price[i])

    def staleness_minutes_at(self, k: int) -> float:
        i = k - self.k0
        if i < 0:
            return NAN
        j = int(self.last_obs[min(i, self.n - 1)])
        return NAN if j < 0 else (i - j) * self.grid.step / 60

    def last_observation_ts(self) -> int | None:
        idx = np.flatnonzero(self.observed)
        return None if idx.size == 0 else self.grid.ts(self.k0 + int(idx[-1]))

    def _print_slot(self, i: int) -> int | None:
        j = int(np.searchsorted(self.pr_local, i))
        return j if j < self.pr_local.size and int(self.pr_local[j]) == i else None

    def prints_at(self, k: int) -> tuple[float, float, float, float]:
        """(volume, notional, min price, max price) of prints in bar k."""
        j = self._print_slot(k - self.k0)
        if j is None:
            return 0.0, 0.0, NAN, NAN
        return (
            float(self.pr_volume[j]),
            float(self.pr_notional[j]),
            float(self.pr_min[j]),
            float(self.pr_max[j]),
        )

    def print_count_at(self, i: int) -> int:
        j = self._print_slot(i)
        return 0 if j is None else int(self.pr_count[j])

    def _dense(self, values: FloatArray, fill: float = 0.0) -> FloatArray:
        out = np.full(self.n, fill)
        out[self.pr_local] = values
        return out

    def dense_volume(self) -> FloatArray:
        return self._dense(self.pr_volume)

    def dense_count(self) -> FloatArray:
        return self._dense(self.pr_count)

    def dense_notional(self) -> FloatArray:
        return self._dense(self.pr_notional)

    def cum_notional(self) -> FloatArray:
        return self.prior_notional + np.cumsum(self.dense_notional())

    def cum_notional_at(self, i: int, prefix: FloatArray | None = None) -> float:
        """`cum_notional()[i]` without materialising the dense array. np.cumsum adds in order and
        adding the dense zeros changes nothing, so the value is bit-identical. `prefix` is
        `np.cumsum(pr_notional)`, for callers that look up many bars."""
        j = int(np.searchsorted(self.pr_local, min(i, self.n - 1), "right"))
        if j == 0:
            return float(self.prior_notional + 0.0)
        partial = prefix[j - 1] if prefix is not None else np.cumsum(self.pr_notional[:j])[-1]
        return float(self.prior_notional + float(partial))

    def last_print(self) -> IntArray:
        mask = np.zeros(self.n, dtype=bool)
        mask[self.pr_local] = True
        return _ffill_index(mask)


def _ffill_index(mask: np.ndarray) -> IntArray:
    idx = np.where(mask, np.arange(mask.size), -1)
    return np.maximum.accumulate(idx) if idx.size else idx.astype(np.int64)


def build_frame(token: TokenData, terms: MarketTerms, grid: Grid) -> TokenFrame | None:
    """Align a token to the grid. Returns None if it has no samples or prints inside the grid."""
    bar_k = grid.bars_of(token.bar_ts) if token.bar_ts.size else np.empty(0, np.int64)
    in_bars = (bar_k >= 0) & (bar_k < grid.n)
    bar_k, bar_p = bar_k[in_bars], token.bar_price[in_bars]

    pr_k = grid.bars_of(token.print_ts) if token.print_ts.size else np.empty(0, np.int64)
    before = pr_k < 0
    prior = token.prior_notional + float(
        np.sum(token.print_price[before] * token.print_size[before])
    )
    in_pr = (pr_k >= 0) & (pr_k < grid.n)
    pr_k = pr_k[in_pr]
    pr_p, pr_s = token.print_price[in_pr], token.print_size[in_pr]

    firsts = [int(a[0]) for a in (bar_k, pr_k) if a.size]
    lasts = [int(a[-1]) for a in (bar_k, pr_k) if a.size]
    if not firsts:
        return None
    k0, k1 = min(firsts), max(lasts)
    n = k1 - k0 + 1

    observed = np.zeros(n, dtype=bool)
    sample = np.full(n, NAN)
    if bar_k.size:
        local = bar_k - k0
        last_in_bucket = np.r_[local[1:] != local[:-1], True]  # last sample per bar wins
        observed[local[last_in_bucket]] = True
        sample[local[last_in_bucket]] = bar_p[last_in_bucket]
    last_obs = _ffill_index(observed)
    price = np.where(last_obs >= 0, sample[np.clip(last_obs, 0, None)], NAN)

    local_pr = pr_k - k0
    uniq, inverse = np.unique(local_pr, return_inverse=True)
    m = uniq.size
    volume, count, notional = np.zeros(m), np.zeros(m), np.zeros(m)
    pmin, pmax = np.full(m, math.inf), np.full(m, -math.inf)
    if m:
        np.add.at(volume, inverse, pr_s)
        np.add.at(count, inverse, 1.0)
        np.add.at(notional, inverse, pr_p * pr_s)
        np.minimum.at(pmin, inverse, pr_p)
        np.maximum.at(pmax, inverse, pr_p)

    return TokenFrame(
        token=token,
        terms=terms,
        grid=grid,
        k0=k0,
        n=n,
        observed=observed,
        price=price,
        last_obs=last_obs.astype(np.int32),
        pr_local=uniq.astype(np.int64),
        pr_volume=volume,
        pr_count=count,
        pr_notional=notional,
        pr_min=pmin,
        pr_max=pmax,
        prior_notional=prior,
    )
