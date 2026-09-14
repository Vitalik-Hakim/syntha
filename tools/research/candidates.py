"""The candidate grid for the M7 strategy search (docs/RESEARCH_PROTOCOL.md).

Every configuration here is pre-registered: the grid is fixed before any of it runs on the
discovery months, and the analysis counts every configuration, so "best of N" is judged against
what the best of N would look like by chance.

Families are hypotheses about prediction-market behaviour, each written once with a small,
coarse grid (no fine-tuning). All use `EITHER`: the entry rules pick the side, so a favourite
rule buys whichever outcome is the favourite. `min_volume_asof` ≥ 10,000 keeps every run
survivorship-free on the liquid collections (docs/DATA_NOTES.md).
"""

from __future__ import annotations

import itertools
from typing import Any

FRESH = {"feature": "staleness_minutes", "params": {}, "op": "lte", "value": 10}
UNIVERSE: dict[str, Any] = {"side": "EITHER", "min_volume_asof": 10000}

HOLD_SIZING = {
    "mode": "PCT_OF_INITIAL",
    "value": 0.02,
    "max_notional_per_trade": 40.0,
    "min_order_policy": "ROUND_UP",
    "round_up_max_multiple": 2.0,
}
HOLD_CONSTRAINTS = {
    "max_open_positions": 40,
    "max_total_exposure_pct": 0.8,
    "max_positions_per_event": 1,
    "cooldown_minutes_per_market": 0,
    "allow_reentry_same_market": False,
}
SWING_SIZING = {
    "mode": "PCT_OF_EQUITY",
    "value": 0.05,
    "max_notional_per_trade": 50.0,
    "min_order_policy": "ROUND_UP",
    "round_up_max_multiple": 2.0,
}
SWING_CONSTRAINTS = {
    "max_open_positions": 10,
    "max_total_exposure_pct": 0.6,
    "max_positions_per_event": 1,
    "cooldown_minutes_per_market": 120,
    "allow_reentry_same_market": True,
}


def cond(feature: str, op: str, value: float, **params: int) -> dict[str, Any]:
    return {"feature": feature, "params": params, "op": op, "value": value}


def _dsl(
    key: str,
    name: str,
    description: str,
    entry: list[dict[str, Any]],
    *,
    exits: list[dict[str, Any]] | None = None,
    hold: bool,
    universe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "dsl": {
            "dsl_version": 1,
            "name": name,
            "description": f"Research candidate — not a trading recommendation. {description}",
            "universe": {**UNIVERSE, **(universe or {})},
            "entry": {"all": [*entry, FRESH]},
            "sizing": HOLD_SIZING if hold else SWING_SIZING,
            "exit": {"any": exits or []},
            "constraints": HOLD_CONSTRAINTS if hold else SWING_CONSTRAINTS,
        },
    }


def grid() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # 1. Favourite to resolution: are near-certain outcomes underpriced late? (favourite–longshot)
    for lo, hours in itertools.product((0.85, 0.90, 0.95), (6, 24, 72)):
        out.append(
            _dsl(
                f"fav_hold_p{lo}_h{hours}",
                f"Favourite to Resolution ≥{lo} within {hours}h",
                f"Buys either outcome priced {lo}–0.985 within {hours} hours of the scheduled end and holds to settlement.",
                [
                    cond("price", "gte", lo),
                    cond("price", "lte", 0.985),
                    cond("time_to_resolution_minutes", "lte", hours * 60),
                ],
                hold=True,
            )
        )

    # 2. Longshot to resolution: the opposite bet.
    for lo, hi, hours in ((0.03, 0.10, 24), (0.03, 0.10, 72), (0.10, 0.20, 24)):
        out.append(
            _dsl(
                f"long_hold_p{lo}-{hi}_h{hours}",
                f"Longshot to Resolution {lo}–{hi} within {hours}h",
                f"Buys either outcome priced {lo}–{hi} within {hours} hours of the scheduled end and holds to settlement, many small positions.",
                [
                    cond("price", "gte", lo),
                    cond("price", "lte", hi),
                    cond("time_to_resolution_minutes", "lte", hours * 60),
                ],
                hold=True,
            )
        )

    # 3. Late drift to resolution: is late information under-reacted to?
    for move, lookback in itertools.product((0.03, 0.05, 0.10), (60, 240)):
        out.append(
            _dsl(
                f"drift_hold_m{move}_l{lookback}",
                f"Late Drift ≥{move:.0%} over {lookback}m to Resolution",
                f"Buys either outcome priced 0.55–0.90 that rose ≥ {move:.0%} over the last {lookback} minutes within 24 hours of the scheduled end, and holds to settlement.",
                [
                    cond("price", "gte", 0.55),
                    cond("price", "lte", 0.90),
                    cond("price_change_pct", "gte", move, lookback_minutes=lookback),
                    cond("time_to_resolution_minutes", "lte", 1440),
                    cond("traded_volume", "gte", 100, lookback_minutes=lookback),
                ],
                hold=True,
            )
        )

    # 4. Overreaction fade: buy sharp falls on heavy volume, exit on a partial rebound.
    for lookback, drop in itertools.product((15, 60), (0.10, 0.15, 0.25)):
        out.append(
            _dsl(
                f"fade_l{lookback}_d{drop}",
                f"Overreaction Fade {drop:.0%} in {lookback}m",
                f"Buys either outcome priced 0.25–0.75 after a fall of ≥ {drop:.0%} in {lookback} minutes on ≥ 3× its usual volume; exits +10%, −15% or after 6 hours.",
                [
                    cond("price", "gte", 0.25),
                    cond("price", "lte", 0.75),
                    cond("price_change_pct", "lte", -drop, lookback_minutes=lookback),
                    cond(
                        "volume_ratio",
                        "gte",
                        3,
                        lookback_minutes=lookback,
                        baseline_minutes=240 if lookback == 15 else 720,
                    ),
                ],
                exits=[
                    {"type": "TAKE_PROFIT_PCT", "value": 0.10},
                    {"type": "STOP_LOSS_PCT", "value": 0.15},
                    {"type": "MAX_HOLD_MINUTES", "value": 360},
                ],
                hold=False,
                universe={"min_time_to_resolution_minutes": 60},
            )
        )

    # 5. Momentum breakout: buy sharp rises on heavy volume, ride with a trailing budget.
    for rise in (0.10, 0.15, 0.25):
        out.append(
            _dsl(
                f"mom_r{rise}",
                f"Momentum Breakout ≥{rise:.0%} in 60m",
                f"Buys either outcome priced 0.30–0.80 that rose ≥ {rise:.0%} in an hour on ≥ 2× its usual volume; exits +20%, −15% or after 12 hours.",
                [
                    cond("price", "gte", 0.30),
                    cond("price", "lte", 0.80),
                    cond("price_change_pct", "gte", rise, lookback_minutes=60),
                    cond("volume_ratio", "gte", 2, lookback_minutes=60, baseline_minutes=720),
                ],
                exits=[
                    {"type": "TAKE_PROFIT_PCT", "value": 0.20},
                    {"type": "STOP_LOSS_PCT", "value": 0.15},
                    {"type": "MAX_HOLD_MINUTES", "value": 720},
                ],
                hold=False,
                universe={"min_time_to_resolution_minutes": 60},
            )
        )

    # 6. VWAP reversion: the sampled price sits below where the market actually traded.
    for gap, lookback in itertools.product((0.02, 0.05), (60, 240)):
        out.append(
            _dsl(
                f"vwap_g{gap}_l{lookback}",
                f"VWAP Reversion gap {gap} over {lookback}m",
                f"Buys either outcome priced 0.20–0.80 whose price is ≥ {gap} below the {lookback}-minute VWAP of trades; exits +5%, −10% or after 4 hours.",
                [
                    cond("price", "gte", 0.20),
                    cond("price", "lte", 0.80),
                    cond("vwap_gap", "lte", -gap, lookback_minutes=lookback),
                    cond("print_count", "gte", 5, lookback_minutes=lookback),
                ],
                exits=[
                    {"type": "TAKE_PROFIT_PCT", "value": 0.05},
                    {"type": "STOP_LOSS_PCT", "value": 0.10},
                    {"type": "MAX_HOLD_MINUTES", "value": 240},
                ],
                hold=False,
                universe={"min_time_to_resolution_minutes": 60},
            )
        )

    # 7. Early favourite: is the favourite underpriced in a market's first day?
    for lo in (0.80, 0.90):
        out.append(
            _dsl(
                f"early_fav_p{lo}",
                f"Early Favourite ≥{lo} to Resolution",
                f"Buys either outcome priced ≥ {lo} within the first 24 hours after the market opened and holds to settlement.",
                [
                    cond("price", "gte", lo),
                    cond("price", "lte", 0.985),
                    cond("time_since_open_minutes", "lte", 1440),
                ],
                hold=True,
            )
        )

    keys = [c["key"] for c in out]
    assert len(keys) == len(set(keys))
    return out


def grid2() -> list[dict[str, Any]]:
    """Second sweep, declared after the first discovery run (docs/RESEARCH_PROTOCOL.md, stage 2).

    Every "late drift" configuration lost money buying the side that rose in the last day; the
    mirror hypothesis is that the side that *fell* is underpriced. Four settings, no tuning of
    anything from sweep 1. Counted separately: N = 4 for its own best-of-N accounting."""
    out: list[dict[str, Any]] = []
    for move, lookback in itertools.product((0.05, 0.10), (60, 240)):
        out.append(
            _dsl(
                f"latefade_hold_m{move}_l{lookback}",
                f"Late Fade ≥{move:.0%} over {lookback}m to Resolution",
                f"Buys either outcome priced 0.10–0.45 that fell ≥ {move:.0%} over the last {lookback} minutes within 24 hours of the scheduled end, and holds to settlement.",
                [
                    cond("price", "gte", 0.10),
                    cond("price", "lte", 0.45),
                    cond("price_change_pct", "lte", -move, lookback_minutes=lookback),
                    cond("time_to_resolution_minutes", "lte", 1440),
                    cond("traded_volume", "gte", 100, lookback_minutes=lookback),
                ],
                hold=True,
            )
        )
    return out


if __name__ == "__main__":
    for c in grid():
        print(c["key"], "—", c["dsl"]["name"])
    print(len(grid()), "configurations")
