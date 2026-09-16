"""Designed recorder watchlist (PRD v3 §3.7, E7).

The v2 recorder followed "top 10 by 24h volume", which records only what is already liquid.
This watchlist is stratified: 4 categories × {binary, multi-outcome} × {thin, liquid}, a fixed
number of markets per stratum, refilled hourly as markets resolve, plus anything added by hand
or by the S4 scanner (`reason` = `structural_candidate`). Membership is point-in-time in
`recorder_watchlist` (`added_at` / `removed_at`), so a later study can know what was being
recorded at any moment.

Gamma fields verified live 2026-09-14 (docs/DATA_NOTES.md): `negRisk`, `feeType`, `volume24hr`,
`acceptingOrders`, `closed`, `clobTokenIds`, `events[0].id`; `/markets?condition_ids=A&
condition_ids=B` returns exactly those markets; `offset` pages the volume-ordered listing.

Choosing what to *record* by current volume is fine: it decides which future data exists.
Backtest universes are still selected point-in-time (PRD §5.1).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg

log = logging.getLogger(__name__)

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
DEFAULT_CATEGORIES = ("sports", "crypto", "politics", "economics")
TARGET_PER_STRATUM = 3  # 4 × 2 × 2 strata × 3 = 48 markets, inside the 40–60 target
LIQUID_MIN_24H = 20_000.0  # USD traded in the last 24h
THIN_MAX_24H = 2_000.0
THIN_MIN_24H = 100.0  # below this there is nothing to record
LISTING_PAGES = 10  # 1,000 markets by 24h volume, down to roughly $4k/day (probe 2026-09-14)
STATUS_BATCH = 20
FINAL_24H_CATEGORY = "politics"
FINAL_24H_SLOTS = 6  # rolling slot: politics markets inside their final 24 hours (S1b cell 1)
FINAL_24H_STRATUM = f"{FINAL_24H_CATEGORY}/final_24h"

_FEE_SUFFIX = re.compile(r"(_fees)?(_v\d+)?$")


def category_of(fee_type: str | None) -> str:
    """Gamma has no category field; the fee type carries one (docs/DATA_NOTES.md "Fees")."""
    return _FEE_SUFFIX.sub("", fee_type) if fee_type else "no_fee"


def tier_of(volume_24h: float) -> str | None:
    if volume_24h >= LIQUID_MIN_24H:
        return "liquid"
    if THIN_MIN_24H <= volume_24h <= THIN_MAX_24H:
        return "thin"
    return None  # the middle band belongs to neither stratum


@dataclass(frozen=True)
class Candidate:
    condition_id: str
    event_id: str | None
    tokens: tuple[str, ...]
    category: str
    structure: str  # binary | multi
    tier: str | None
    volume_24h: float
    open: bool
    end_ts: datetime | None = None

    @property
    def stratum(self) -> str:
        return f"{self.category}/{self.structure}/{self.tier}"

    def in_final_24h(self, now: datetime) -> bool:
        """Scheduled to end within the next 24 hours (and not yet past its end)."""
        return self.end_ts is not None and now < self.end_ts <= now + timedelta(hours=24)


def _end_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def classify(market: dict[str, Any]) -> Candidate | None:
    """A Gamma market row as a watchlist candidate; None when it cannot be recorded."""
    tokens = tuple(str(t) for t in json.loads(market.get("clobTokenIds") or "[]"))
    if not tokens or market.get("enableOrderBook") is False:
        return None
    events = market.get("events") or []
    volume = float(market.get("volume24hr") or 0.0)
    return Candidate(
        condition_id=str(market.get("conditionId")),
        event_id=str(events[0]["id"]) if events and events[0].get("id") is not None else None,
        tokens=tokens,
        category=category_of(market.get("feeType")),
        structure="multi" if market.get("negRisk") else "binary",
        tier=tier_of(volume),
        volume_24h=volume,
        open=bool(market.get("acceptingOrders", True)) and not bool(market.get("closed")),
        end_ts=_end_ts(market.get("endDate")),
    )


@dataclass(frozen=True)
class Plan:
    add: list[Candidate]
    remove: list[str]  # condition ids whose rows are retired


def plan(
    active: dict[str, str],  # condition_id → stratum, currently watched
    candidates: list[Candidate],
    still_open: dict[str, bool],  # condition_id → open, for the active ones
    *,
    target: int = TARGET_PER_STRATUM,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    final_24h_slots: int = 0,
    now: datetime | None = None,
) -> Plan:
    """Retire watched markets that closed; fill every stratum below `target` from the most
    liquid open candidates not yet watched; then fill the rolling `politics/final_24h` slot
    (markets of that category scheduled to end within 24 hours, any tier with volume) up to
    `final_24h_slots`. Pure: no I/O, so it is testable."""
    now = now or datetime.now(UTC)
    remove = [cid for cid in sorted(active) if not still_open.get(cid, True)]
    kept: dict[str, int] = {}
    for cid, stratum in active.items():
        if cid not in remove:
            kept[stratum] = kept.get(stratum, 0) + 1
    add: list[Candidate] = []
    pool = sorted(
        (c for c in candidates if c.open and c.tier and c.category in categories),
        key=lambda c: -c.volume_24h,
    )
    for category in categories:
        for structure in ("binary", "multi"):
            for tier in ("thin", "liquid"):
                stratum = f"{category}/{structure}/{tier}"
                need = target - kept.get(stratum, 0)
                for c in pool:
                    if need <= 0:
                        break
                    if c.stratum == stratum and c.condition_id not in active:
                        add.append(c)
                        need -= 1
    need = final_24h_slots - kept.get(FINAL_24H_STRATUM, 0)
    taken = {c.condition_id for c in add}
    for c in sorted(candidates, key=lambda c: -c.volume_24h):
        if need <= 0:
            break
        if (
            c.open
            and c.category == FINAL_24H_CATEGORY
            and c.volume_24h >= THIN_MIN_24H
            and c.in_final_24h(now)
            and c.condition_id not in active
            and c.condition_id not in taken
        ):
            add.append(FinalSlot(c))
            taken.add(c.condition_id)
            need -= 1
    return Plan(add, remove)


class FinalSlot(Candidate):
    """A candidate watched through the rolling final-24h slot: its stratum names the slot."""

    def __init__(self, c: Candidate) -> None:
        object.__setattr__(self, "condition_id", c.condition_id)
        object.__setattr__(self, "event_id", c.event_id)
        object.__setattr__(self, "tokens", c.tokens)
        object.__setattr__(self, "category", c.category)
        object.__setattr__(self, "structure", c.structure)
        object.__setattr__(self, "tier", c.tier)
        object.__setattr__(self, "volume_24h", c.volume_24h)
        object.__setattr__(self, "open", c.open)
        object.__setattr__(self, "end_ts", c.end_ts)

    @property
    def stratum(self) -> str:
        return FINAL_24H_STRATUM


# ------------------------------------------------------------------ I/O


def fetch_markets(client: httpx.Client, pages: int = LISTING_PAGES) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(pages):
        resp = client.get(
            GAMMA_MARKETS,
            params={
                "closed": "false",
                "active": "true",
                "limit": 100,
                "offset": page * 100,
                "order": "volume24hr",
                "ascending": "false",
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        out.extend(rows)
        if len(rows) < 100:
            break
    return out


def fetch_ending_soon(
    client: httpx.Client, now: datetime, hours: int = 24, pages: int = 3
) -> list[dict[str, Any]]:
    """Open markets whose scheduled end falls within `hours` (Gamma `end_date_min/max`, verified
    on the closed listing in docs/DATA_NOTES.md "M9 prep" and live on the open listing
    2026-09-16), most traded first — the pool for the final-24h slot, which the volume-ordered
    top-1,000 listing would miss for a quiet market."""
    out: list[dict[str, Any]] = []
    for page in range(pages):
        resp = client.get(
            GAMMA_MARKETS,
            params={
                "closed": "false",
                "active": "true",
                "limit": 100,
                "offset": page * 100,
                "order": "volume24hr",
                "ascending": "false",
                "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        out.extend(rows)
        if len(rows) < 100:
            break
    return out


def fetch_status(client: httpx.Client, condition_ids: list[str]) -> dict[str, bool]:
    """Whether each watched market is still open, from Gamma. Missing → treated as closed."""
    status: dict[str, bool] = {}
    for i in range(0, len(condition_ids), STATUS_BATCH):
        batch = condition_ids[i : i + STATUS_BATCH]
        resp = client.get(GAMMA_MARKETS, params=[("condition_ids", cid) for cid in batch])
        resp.raise_for_status()
        for m in resp.json():
            cand = classify(m)
            if cand is not None:
                status[cand.condition_id] = cand.open
        for cid in batch:
            status.setdefault(cid, False)
    return status


def load_active(conn: psycopg.Connection[Any]) -> tuple[dict[str, str], set[str]]:
    """(condition_id → stratum, watched token ids) for rows without removed_at."""
    rows = conn.execute(
        "SELECT condition_id, stratum, clob_token_id FROM recorder_watchlist WHERE removed_at IS NULL"
    ).fetchall()
    return {r[0]: r[1] for r in rows}, {r[2] for r in rows}


def apply(conn: psycopg.Connection[Any], p: Plan, now: datetime, reason: str) -> None:
    with conn.transaction():
        for cid in p.remove:
            conn.execute(
                "UPDATE recorder_watchlist SET removed_at = %s WHERE condition_id = %s AND removed_at IS NULL",
                (now, cid),
            )
        for c in p.add:
            for tok in c.tokens:
                conn.execute(
                    "INSERT INTO recorder_watchlist (clob_token_id, condition_id, event_id, stratum, "
                    "reason, added_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (tok, c.condition_id, c.event_id, c.stratum, reason, now),
                )


def add_manual(
    conn: psycopg.Connection[Any], client: httpx.Client, condition_ids: list[str], reason: str
) -> int:
    """Watch specific markets (`manual`, or `structural_candidate` from the S4 scanner)."""
    now = datetime.now(UTC)
    active, _ = load_active(conn)
    added = 0
    for i in range(0, len(condition_ids), STATUS_BATCH):
        batch = [c for c in condition_ids[i : i + STATUS_BATCH] if c not in active]
        if not batch:
            continue
        resp = client.get(GAMMA_MARKETS, params=[("condition_ids", cid) for cid in batch])
        resp.raise_for_status()
        cands = [c for c in (classify(m) for m in resp.json()) if c is not None]
        apply(conn, Plan(cands, []), now, reason)
        added += len(cands)
    return added


def refresh(
    database_url: str,
    *,
    target: int = TARGET_PER_STRATUM,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    final_24h_slots: int = FINAL_24H_SLOTS,
    now: datetime | None = None,
) -> set[str]:
    """One refill cycle: retire closed markets, fill strata and the final-24h slot, return the
    token ids to record."""
    now = now or datetime.now(UTC)
    with psycopg.connect(database_url) as conn, httpx.Client(timeout=30) as client:
        active, _ = load_active(conn)
        listing = fetch_markets(client)
        if final_24h_slots > 0:
            seen = {m.get("conditionId") for m in listing}
            listing += [
                m for m in fetch_ending_soon(client, now) if m.get("conditionId") not in seen
            ]
        candidates = [c for c in (classify(m) for m in listing) if c is not None]
        status = fetch_status(client, sorted(active)) if active else {}
        p = plan(
            active,
            candidates,
            status,
            target=target,
            categories=categories,
            final_24h_slots=final_24h_slots,
            now=now,
        )
        apply(conn, p, now, "stratum_fill")
        strata, tokens = load_active(conn)
        log.info(
            "watchlist: %d markets / %d tokens (+%d, -%d); strata: %s",
            len(strata),
            len(tokens),
            len(p.add),
            len(p.remove),
            ", ".join(f"{s}={n}" for s, n in sorted(_counts(strata).items())),
        )
        return tokens


def _counts(strata: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in strata.values():
        out[s] = out.get(s, 0) + 1
    return out
