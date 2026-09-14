"""Polymarket endpoints, as verified live on 2026-09-14 (docs/DATA_NOTES.md).

Endpoint names double as raw-layer directory names; changing one orphans existing raw data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from syntha_ingestion.config import (
    CLOB_URL,
    DATA_URL,
    GAMMA_URL,
    PRICE_WINDOW_SECONDS,
    RESOLUTION_BATCH,
)
from syntha_ingestion.http import ApiClient
from syntha_ingestion.raw_store import RawRecord, RawStore

GAMMA_MARKETS_KEYSET = "gamma_markets_keyset"
GAMMA_MARKET = "gamma_market"
GAMMA_MARKETS_BY_ID = "gamma_markets_by_id"
MARKET_ID_BATCH = 50
GAMMA_EVENT = "gamma_event"
CLOB_PRICES_HISTORY = "clob_prices_history"
DATA_V2_TRADES = "data_v2_trades"
DATA_V2_RESOLUTIONS = "data_v2_resolutions"


async def list_markets(
    client: ApiClient, *, end_date_min: str, end_date_max: str, closed: bool
) -> AsyncIterator[dict[str, Any]]:
    """Every market whose scheduled end date falls in [min, max], via keyset pagination."""
    cursor: str | None = None
    page = 0
    while True:
        params = {
            "closed": "true" if closed else "false",
            "limit": 100,  # server maximum since 2026-05-14
            "end_date_min": end_date_min,
            "end_date_max": end_date_max,
            "after_cursor": cursor,
        }
        unit = {
            "kind": "listing",
            "end_date_min": end_date_min,
            "end_date_max": end_date_max,
            "closed": closed,
            "page": page,
        }
        _, body = await client.get(
            GAMMA_MARKETS_KEYSET, f"{GAMMA_URL}/markets/keyset", params, unit
        )
        for market in body.get("markets") or []:
            yield market
        cursor = body.get("next_cursor")
        page += 1
        if not cursor or not body.get("markets"):
            return


async def fetch_market(client: ApiClient, market_id: str) -> dict[str, Any]:
    """Single market. NOTE: this response omits `events`; prefer `fetch_markets_by_id`."""
    _, body = await client.get(
        GAMMA_MARKET, f"{GAMMA_URL}/markets/{market_id}", {}, {"kind": "market", "id": market_id}
    )
    result: dict[str, Any] = body
    return result


async def fetch_markets_by_id(
    client: ApiClient, market_ids: Sequence[str], *, closed: bool
) -> list[dict[str, Any]]:
    """Full market objects, including `events`, via the listing endpoint's repeated `id` filter.

    The listing applies its `closed` filter on top of `id`, so closed and open markets need
    separate calls.
    """
    if len(market_ids) > MARKET_ID_BATCH:
        raise ValueError(f"at most {MARKET_ID_BATCH} market ids per call")
    params = {
        "id": list(market_ids),
        "closed": "true" if closed else "false",
        "limit": len(market_ids),
    }
    unit = {"kind": "markets_by_id", "ids": list(market_ids), "closed": closed}
    _, body = await client.get(GAMMA_MARKETS_BY_ID, f"{GAMMA_URL}/markets", params, unit)
    rows: list[dict[str, Any]] = body or []
    return rows


def latest_market_objects(raw: RawStore) -> dict[str, tuple[RawRecord, dict[str, Any]]]:
    """Newest Gamma object per market id across both market endpoints.

    If the newest object lacks `events` (a single-market response), `events` is carried over
    from the newest object that has it.
    """
    records = sorted(
        raw.records(GAMMA_MARKET) + raw.records(GAMMA_MARKETS_BY_ID), key=lambda r: r.sort_key
    )
    out: dict[str, tuple[RawRecord, dict[str, Any]]] = {}
    events: dict[str, Any] = {}
    for rec in records:
        if rec.status != 200:
            continue
        body = rec.json()
        for market in body if isinstance(body, list) else [body]:
            mid = str(market["id"])
            if market.get("events"):
                events[mid] = market["events"]
            out[mid] = (rec, market)
    for mid, (rec, market) in out.items():
        if not market.get("events") and mid in events:
            out[mid] = (rec, {**market, "events": events[mid]})
    return out


async def fetch_event(client: ApiClient, event_id: str) -> dict[str, Any]:
    _, body = await client.get(
        GAMMA_EVENT, f"{GAMMA_URL}/events/{event_id}", {}, {"kind": "event", "id": event_id}
    )
    result: dict[str, Any] = body
    return result


def price_windows(start_ts: int, end_ts: int) -> list[tuple[int, int]]:
    """Split [start, end) into consecutive windows the CLOB will accept."""
    if end_ts <= start_ts:
        return []
    windows = []
    s = start_ts
    while s < end_ts:
        e = min(s + PRICE_WINDOW_SECONDS, end_ts)
        windows.append((s, e))
        s = e
    return windows


async def fetch_price_window(client: ApiClient, token_id: str, start_ts: int, end_ts: int) -> int:
    """1-minute price samples for one token and window. Returns the number of points."""
    params = {"market": token_id, "startTs": start_ts, "endTs": end_ts, "fidelity": 1}
    unit = {"kind": "prices", "token": token_id, "start": start_ts, "end": end_ts}
    _, body = await client.get(CLOB_PRICES_HISTORY, f"{CLOB_URL}/prices-history", params, unit)
    return len(body.get("history") or [])


async def walk_trades(client: ApiClient, condition_id: str) -> int:
    """All taker trade prints for a market. Returns the number of prints.

    The v2 cursor carries only the seek position, so `condition` MUST be re-sent on every page;
    a bare cursor returns an empty page that looks like the end of the data.
    """
    cursor: str | None = None
    page = 0
    total = 0
    while True:
        params = {"condition": condition_id, "limit": 1000, "cursor": cursor}
        unit = {"kind": "trades", "condition": condition_id, "page": page}
        _, body = await client.get(DATA_V2_TRADES, f"{DATA_URL}/v2/trades", params, unit)
        total += len(body.get("data") or [])
        cursor = (body.get("pagination") or {}).get("next_cursor")
        page += 1
        if not cursor:
            return total


def resolution_batches(condition_ids: Sequence[str]) -> list[list[str]]:
    ids = sorted(set(condition_ids))
    return [ids[i : i + RESOLUTION_BATCH] for i in range(0, len(ids), RESOLUTION_BATCH)]


async def fetch_resolutions(
    client: ApiClient, condition_ids: Sequence[str]
) -> list[dict[str, Any]]:
    if len(condition_ids) > RESOLUTION_BATCH:
        raise ValueError(f"at most {RESOLUTION_BATCH} condition ids per call")
    params = {"condition": ",".join(condition_ids)}
    unit = {"kind": "resolutions", "conditions": list(condition_ids)}
    _, body = await client.get(DATA_V2_RESOLUTIONS, f"{DATA_URL}/v2/resolutions", params, unit)
    rows: list[dict[str, Any]] = body.get("data") or []
    return rows
