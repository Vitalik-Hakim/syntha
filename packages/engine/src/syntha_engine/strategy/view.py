"""What a strategy is allowed to see (PRD §5.2). Look-ahead is structurally impossible here, not
merely avoided by convention:

- `BarWindow` holds an immutable *copy* of at most `lookback + 1` bars ending at `now`. It is
  indexable only with negative offsets (`bars[-1]` is the current bar); there is no reference
  to the underlying arrays, so neither `.base` nor slicing reaches the future.
- `FeatureView` holds values at `now` only, for the features the strategy declared; any other
  key raises.
- `MarketStatic` is a frozen slots dataclass that physically has no resolution, resolution time,
  close time or final volume.

tests/test_lookahead_adversarial.py attacks each of these.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple


class LookAheadError(Exception):
    """Raised when a strategy tries to read information from the future."""


class Bar(NamedTuple):
    ts: datetime  # bar open; the bar's data is complete at ts + interval
    price: float | None
    observed: bool
    volume: float
    prints: int


class BarWindow:
    __slots__ = ("_bars",)
    _bars: tuple[Bar, ...]

    def __init__(self, bars: tuple[Bar, ...]) -> None:
        object.__setattr__(self, "_bars", bars)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("BarWindow is immutable")

    def __getitem__(self, offset: int) -> Bar:
        if isinstance(offset, slice):
            raise TypeError("BarWindow does not support slicing; use negative integer offsets")
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise TypeError("BarWindow offsets must be integers")
        if offset >= 0:
            raise LookAheadError(
                f"bars[{offset}] would reach the future; bars[-1] is the current bar"
            )
        if -offset > len(self._bars):
            raise IndexError(f"bars[{offset}] is beyond the strategy's declared lookback")
        return self._bars[offset]

    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self) -> Iterator[Bar]:
        raise TypeError("iterate with negative offsets: bars[-1], bars[-2], …")


class FeatureView(Mapping[str, float | None]):
    __slots__ = ("_values",)
    _values: dict[str, float | None]

    def __init__(self, values: dict[str, float | None]) -> None:
        object.__setattr__(self, "_values", dict(values))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FeatureView is immutable")

    def __getitem__(self, key: str) -> float | None:
        try:
            return self._values[key]
        except KeyError:
            raise KeyError(
                f"feature {key!r} is not available: only declared features at the current bar are"
            ) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass(frozen=True, slots=True)
class MarketStatic:
    """Pre-trade facts only. Deliberately has no resolution, resolved_at, final_volume or
    end_date_actual attribute (PRD §5.2)."""

    condition_id: str
    clob_token_id: str
    outcome_index: int
    outcome_name: str
    category: str | None
    tick_size: float
    min_order_size: float | None
    open_ts: datetime | None


@dataclass(frozen=True, slots=True)
class PositionView:
    entry_ts: datetime
    entry_price: float
    shares: float
    holding_minutes: float


@dataclass(frozen=True, slots=True)
class MarketView:
    now: datetime  # open of the current bar (its data is complete at the bar close)
    price: float | None
    bars: BarWindow
    features: FeatureView
    market: MarketStatic
    position: PositionView | None
