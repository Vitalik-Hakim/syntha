"""Fill models (PRD §11). Pipeline:

    reference price → model adjustment → slippage → tick quantisation → bounds

Quantisation rounds *against* the trader: up to the tick for buys, down for sells. Bounds clamp
to [tick, 1 − tick]. Fees are applied by the portfolio on the final price.

Every refusal is explicit: the quote carries status UNEXECUTABLE and a reason. Nothing is
interpolated or assumed to fill (PRD §0 rule 2).
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Literal

from syntha_contracts.backtest import ExecutionConfig
from syntha_contracts.enums import ExecutionModel, FillStatus
from syntha_engine.grid import TokenFrame

Direction = Literal["BUY", "SELL"]

# Refusal reasons (recorded in traces and counted in data quality)
NO_PRICE = "NO_PRICE"
STALE_PRICE = "STALE_PRICE"
NO_PRINTS = "NO_PRINTS"
LIQUIDITY_CAP = "LIQUIDITY_CAP"
MARKET_CLOSED = "MARKET_CLOSED"
MIN_SIZE_UNKNOWN = "MIN_SIZE_UNKNOWN"
END_OF_DATA = "END_OF_DATA"


@functools.lru_cache(maxsize=1 << 16)
def to_decimal(x: float) -> Decimal:
    return Decimal(f"{x:.6f}")


@dataclass(frozen=True, slots=True)
class Quote:
    status: FillStatus  # FILLED (priceable) or UNEXECUTABLE
    reason: str | None = None
    ref_price: Decimal | None = None
    fill_price: Decimal | None = None
    max_shares: Decimal | None = None  # liquidity cap; None = unlimited
    detail: dict[str, Any] = field(default_factory=dict)


def _unexecutable(reason: str, **detail: Any) -> Quote:
    return Quote(FillStatus.UNEXECUTABLE, reason, detail=detail)


def _finish(
    direction: Direction,
    ref: Decimal,
    adjusted: Decimal,
    tick: Decimal,
    cfg: ExecutionConfig,
    slippage_multiplier: int,
    detail: dict[str, Any],
    max_shares: Decimal | None,
) -> Quote:
    sign = 1 if direction == "BUY" else -1
    if cfg.slippage_ticks is not None:
        ticks = cfg.slippage_ticks * slippage_multiplier
        slipped = adjusted + sign * ticks * tick
        detail["slippage_ticks"] = ticks
    else:
        assert cfg.slippage_bps is not None
        bps = Decimal(str(cfg.slippage_bps)) * slippage_multiplier
        slipped = adjusted * (1 + sign * bps / Decimal(10_000))
        detail["slippage_bps"] = float(bps)
    rounding = ROUND_CEILING if direction == "BUY" else ROUND_FLOOR
    quantised = (slipped / tick).to_integral_value(rounding=rounding) * tick
    fill = min(max(quantised, tick), 1 - tick)
    detail.update(ref_price=float(ref), fill_price=float(fill))
    return Quote(FillStatus.FILLED, None, ref, fill, max_shares, detail)


def quote(
    direction: Direction,
    frame: TokenFrame,
    k: int,
    cfg: ExecutionConfig,
    *,
    max_stale_minutes: int,
    trading_closed: bool,
) -> Quote:
    tick = frame.terms.tick_size
    if trading_closed:
        return _unexecutable(MARKET_CLOSED)
    if k >= frame.grid.n:
        return _unexecutable(END_OF_DATA)

    def last_price_quote(multiplier: int, detail: dict[str, Any]) -> Quote:
        p = frame.price_at(k)
        if math.isnan(p):
            return _unexecutable(NO_PRICE, **detail)
        staleness = frame.staleness_minutes_at(k)
        if staleness > max_stale_minutes:
            return _unexecutable(STALE_PRICE, staleness_minutes=staleness, **detail)
        ref = to_decimal(p)
        return _finish(direction, ref, ref, tick, cfg, multiplier, detail, None)

    if cfg.model == ExecutionModel.LAST_PRICE:
        return last_price_quote(1, {"model": "LAST_PRICE"})

    volume, notional, pmin, pmax = frame.prints_at(k)
    detail: dict[str, Any] = {"model": "TRADE_PRINT", "bar_print_volume": volume}
    if volume <= 0:
        if cfg.allow_fill_on_no_prints:
            detail["fallback"] = "LAST_PRICE_NO_PRINTS"
            return last_price_quote(2, detail)  # doubled slippage penalty (PRD §11.3)
        return _unexecutable(NO_PRINTS, **detail)

    vwap = to_decimal(notional / volume)
    detail["vwap"] = float(vwap)
    adjusted = vwap
    if cfg.adverse_selection:
        adjusted = to_decimal(pmax if direction == "BUY" else pmin)
        detail["worst_print"] = float(adjusted)
    cap = None
    if cfg.liquidity_participation is not None:
        cap = to_decimal(cfg.liquidity_participation * volume)
        detail["cap"] = float(cap)
    return _finish(direction, vwap, adjusted, tick, cfg, 1, detail, cap)
