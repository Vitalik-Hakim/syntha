"""Feature registry (PRD §8.2).

Every feature is a pure, *causal* function of a token's bar arrays: the value at bar i uses
only bars ≤ i. `tests/test_features_causal.py` proves it for every registered feature by
recomputing on truncated data. NaN means null; a comparison against null is false
(PRD §8.2 null policy).

Lookbacks are given in minutes and must be a positive multiple of the bar interval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np

from syntha_engine.data import FloatArray
from syntha_engine.grid import TokenFrame

NAN = float("nan")

FeatureFn = Callable[[TokenFrame, Mapping[str, int]], FloatArray]


class FeatureError(ValueError):
    """Unknown feature, or invalid parameters for one."""


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    params: tuple[str, ...]
    fn: FeatureFn
    definition: str
    null_when: str


def feature_key(name: str, params: Mapping[str, int]) -> str:
    if not params:
        return name
    inner = ",".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{name}({inner})"


def _bars(frame: TokenFrame, minutes: int) -> int:
    step_min = frame.grid.step // 60
    return minutes // step_min


def _rolling_sum(x: FloatArray, w: int) -> FloatArray:
    c = np.concatenate(([0.0], np.cumsum(x)))
    i = np.arange(1, x.size + 1)
    out: FloatArray = c[i] - c[np.maximum(i - w, 0)]
    return out


def _lagged_price(frame: TokenFrame, lag: int) -> FloatArray:
    i = np.arange(frame.n)
    j = i - lag
    return np.where(j >= 0, frame.price[np.clip(j, 0, None)], NAN)


def _close_ts(frame: TokenFrame) -> FloatArray:
    return (frame.grid.start + (frame.k0 + np.arange(frame.n) + 1) * frame.grid.step).astype(
        np.float64
    )


# ------------------------------------------------------------------ features


def price(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    return frame.price.copy()


def price_change_pct(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    prev = _lagged_price(frame, _bars(frame, p["lookback_minutes"]))
    with np.errstate(divide="ignore", invalid="ignore"):
        out = frame.price / prev - 1.0
    return np.where(prev > 0, out, NAN)


def price_change_abs(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    prev = _lagged_price(frame, _bars(frame, p["lookback_minutes"]))
    return (frame.price - prev) * 100.0  # cents


def traded_volume(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    return _rolling_sum(frame.dense_volume(), _bars(frame, p["lookback_minutes"]))


def print_count(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    return _rolling_sum(frame.dense_count(), _bars(frame, p["lookback_minutes"]))


def volume_ratio(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    lb, bb = _bars(frame, p["lookback_minutes"]), _bars(frame, p["baseline_minutes"])
    dense = frame.dense_volume()
    window = _rolling_sum(dense, lb)
    baseline = _rolling_sum(dense, bb) * lb / bb
    enough = np.arange(frame.n) + 1 >= bb
    with np.errstate(divide="ignore", invalid="ignore"):
        out = window / baseline
    return np.where(enough & (baseline > 0), out, NAN)


def vwap_gap(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    lb = _bars(frame, p["lookback_minutes"])
    vol = _rolling_sum(frame.dense_volume(), lb)
    notional = _rolling_sum(frame.dense_notional(), lb)
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = notional / vol
    return np.where(vol > 0, frame.price - vwap, NAN)


def realized_vol(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    lb = _bars(frame, p["lookback_minutes"])
    prev = _lagged_price(frame, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.log(frame.price / prev)
    ok = np.isfinite(r)
    r0 = np.where(ok, r, 0.0)
    n_ok = _rolling_sum(ok.astype(np.float64), lb)
    s1, s2 = _rolling_sum(r0, lb), _rolling_sum(r0 * r0, lb)
    if lb < 2:
        return np.full(frame.n, NAN)
    var = np.maximum((s2 - s1 * s1 / lb) / (lb - 1), 0.0)
    return np.where(n_ok == lb, np.sqrt(var), NAN)


def time_to_resolution_minutes(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    end = frame.terms.end_ts
    if end is None:
        return np.full(frame.n, NAN)
    return (end - _close_ts(frame)) / 60.0


def time_since_open_minutes(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    start = frame.terms.open_ts
    if start is None:
        return np.full(frame.n, NAN)
    return (_close_ts(frame) - start) / 60.0


def spread_proxy(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    # v1 datasets carry no historical order book (PRD §4.2): always null.
    return np.full(frame.n, NAN)


def staleness_minutes(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    i = np.arange(frame.n)
    return np.where(frame.last_obs >= 0, (i - frame.last_obs) * frame.grid.step / 60.0, NAN)


def minutes_since_last_print(frame: TokenFrame, p: Mapping[str, int]) -> FloatArray:
    i = np.arange(frame.n)
    last = frame.last_print()
    return np.where(last >= 0, (i - last) * frame.grid.step / 60.0, NAN)


LB = ("lookback_minutes",)

REGISTRY: dict[str, FeatureSpec] = {
    s.name: s
    for s in (
        FeatureSpec(
            "price",
            (),
            price,
            "Last sampled price at or before the bar (forward-filled)",
            "no sample yet",
        ),
        FeatureSpec(
            "price_change_pct",
            LB,
            price_change_pct,
            "p_t / p_{t−L} − 1",
            "fewer than L minutes of price history, or p_{t−L} ≤ 0",
        ),
        FeatureSpec(
            "price_change_abs",
            LB,
            price_change_abs,
            "(p_t − p_{t−L}) in cents",
            "fewer than L minutes of price history",
        ),
        FeatureSpec(
            "traded_volume",
            LB,
            traded_volume,
            "Σ printed shares over the last L minutes",
            "never (0 when no prints)",
        ),
        FeatureSpec(
            "volume_ratio",
            ("lookback_minutes", "baseline_minutes"),
            volume_ratio,
            "window volume ÷ average window volume over the baseline",
            "baseline history shorter than required, or zero baseline volume",
        ),
        FeatureSpec(
            "print_count", LB, print_count, "number of prints over the last L minutes", "never (0)"
        ),
        FeatureSpec(
            "vwap_gap",
            LB,
            vwap_gap,
            "p_t − VWAP of prints over the last L minutes",
            "no prints in the window",
        ),
        FeatureSpec(
            "realized_vol",
            LB,
            realized_vol,
            "sample stdev of per-bar log returns over L minutes",
            "fewer than L valid returns, or L < 2 bars",
        ),
        FeatureSpec(
            "time_to_resolution_minutes",
            (),
            time_to_resolution_minutes,
            "scheduled end − bar close",
            "scheduled end unknown",
        ),
        FeatureSpec(
            "time_since_open_minutes",
            (),
            time_since_open_minutes,
            "bar close − market open",
            "open time unknown",
        ),
        FeatureSpec(
            "spread_proxy",
            (),
            spread_proxy,
            "from recorded order book",
            "always in v1 (no historical L2)",
        ),
        FeatureSpec(
            "staleness_minutes",
            (),
            staleness_minutes,
            "minutes since the last price sample",
            "no sample yet",
        ),
        FeatureSpec(
            "minutes_since_last_print",
            (),
            minutes_since_last_print,
            "minutes since the last trade print",
            "no print yet",
        ),
    )
}


def validate(name: str, params: Mapping[str, int], step_minutes: int) -> int:
    """Check a feature reference; return the lookback it needs, in bars."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise FeatureError(f"unknown feature {name!r}")
    if set(params) != set(spec.params):
        raise FeatureError(f"{name} takes params {sorted(spec.params)}, got {sorted(params)}")
    needed = 0
    for key, minutes in params.items():
        if minutes <= 0 or minutes % step_minutes:
            raise FeatureError(
                f"{name}.{key}={minutes}: must be a positive multiple of the {step_minutes}-minute bar"
            )
        needed = max(needed, minutes // step_minutes)
    if name == "volume_ratio" and params["baseline_minutes"] < params["lookback_minutes"]:
        raise FeatureError("volume_ratio.baseline_minutes must be >= lookback_minutes")
    return needed


def compute(frame: TokenFrame, name: str, params: Mapping[str, int]) -> FloatArray:
    return REGISTRY[name].fn(frame, params)
