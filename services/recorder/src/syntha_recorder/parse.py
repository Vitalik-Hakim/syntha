"""Pure translation of market-websocket frames into rows. No I/O.

Wire format verified live on 2026-09-14 (docs/DATA_NOTES.md): the initial snapshot arrives as a
JSON array, later frames as single objects; `book` levels arrive bids-ascending and
asks-descending and are re-sorted here to the PRD's convention (bids descending, asks
ascending). Prices and sizes are decimal strings and stay strings until Postgres parses them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class SnapshotRow:
    clob_token_id: str
    ts: datetime
    condition_id: str | None
    bids: list[list[str]]
    asks: list[list[str]]
    book_hash: str | None
    received_at: datetime


@dataclass(frozen=True)
class DeltaRow:
    ts: datetime
    clob_token_id: str
    condition_id: str | None
    side: str
    price: str
    size: str
    best_bid: str | None
    best_ask: str | None
    received_at: datetime


def _ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    except (TypeError, ValueError):
        return None


def _decimal_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        Decimal(str(value))
    except InvalidOperation:
        return None
    return str(value)


def _levels(levels: Any, descending: bool) -> list[list[str]]:
    out = []
    for lvl in levels or []:
        price, size = _decimal_str(lvl.get("price")), _decimal_str(lvl.get("size"))
        if price is not None and size is not None:
            out.append([price, size])
    out.sort(key=lambda ps: Decimal(ps[0]), reverse=descending)
    return out


def events(frame: str) -> list[dict[str, Any]]:
    """Decode one text frame into event dicts. `PONG` and non-JSON frames yield nothing."""
    if frame == "PONG":
        return []
    try:
        data = json.loads(frame)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    return [i for i in items if isinstance(i, dict)]


def rows(
    event: dict[str, Any], received_at: datetime, watched: set[str]
) -> tuple[list[SnapshotRow], list[DeltaRow]]:
    """Rows for one event, restricted to watched tokens. Unknown event types yield nothing."""
    kind = event.get("event_type")
    ts = _ms(event.get("timestamp")) or received_at
    market = event.get("market")

    if kind == "book":
        token = str(event.get("asset_id"))
        if token not in watched:
            return [], []
        return [
            SnapshotRow(
                clob_token_id=token,
                ts=ts,
                condition_id=market,
                bids=_levels(event.get("bids"), descending=True),
                asks=_levels(event.get("asks"), descending=False),
                book_hash=event.get("hash"),
                received_at=received_at,
            )
        ], []

    if kind == "price_change":
        deltas = []
        for change in event.get("price_changes") or []:
            token = str(change.get("asset_id"))
            side = str(change.get("side", "")).upper()
            price, size = _decimal_str(change.get("price")), _decimal_str(change.get("size"))
            if token not in watched or side not in ("BUY", "SELL") or price is None or size is None:
                continue
            deltas.append(
                DeltaRow(
                    ts=ts,
                    clob_token_id=token,
                    condition_id=market,
                    side=side,
                    price=price,
                    size=size,
                    best_bid=_decimal_str(change.get("best_bid")),
                    best_ask=_decimal_str(change.get("best_ask")),
                    received_at=received_at,
                )
            )
        return [], deltas

    return [], []
