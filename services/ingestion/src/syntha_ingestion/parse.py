"""Pure translation of Polymarket payloads into Syntha's domain values. No I/O.

Every rule here traces to a verified finding in docs/DATA_NOTES.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# /v2/resolutions `price` is an 18-decimal fixed-point payout for outcome 0.
ONE_E18 = 10**18
HALF_E18 = 5 * 10**17

_FEE_SUFFIX = re.compile(r"(_fees)?(_v\d+)?$")
_FEE_CATEGORY = {
    "crypto_15_min": "crypto",
    "finance_prices": "finance",
    "general": "other",
}


def fee_category(fee_type: str | None) -> str:
    """Category from Gamma `feeType` (e.g. `sports_fees_v3` → `sports`). None → `no_fee`."""
    if not fee_type:
        return "no_fee"
    if fee_type in _FEE_CATEGORY:
        return _FEE_CATEGORY[fee_type]
    stem = _FEE_SUFFIX.sub("", fee_type)
    return _FEE_CATEGORY.get(stem, stem)


def parse_ts(value: Any) -> datetime | None:
    """Gamma/Data API timestamps: ISO-8601 with Z, Postgres-style `2026-04-09 00:28:21+00`,
    or epoch seconds (int or numeric string)."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value), UTC)
    text = str(value).strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif re.search(r"[+-]\d{2}$", text):
        text += ":00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def json_list(value: Any) -> list[Any]:
    """Gamma encodes `outcomes`, `clobTokenIds`, `outcomePrices` as JSON strings."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON list, got {value!r}")
    return parsed


@dataclass(frozen=True)
class Resolution:
    resolution: str  # YES | NO | INVALID | UNRESOLVED | OUTCOME_<n>
    resolved_at: datetime | None
    anomaly: str | None = None


def _label(outcomes: list[str], index: int) -> str:
    names = [o.strip().lower() for o in outcomes]
    if names == ["yes", "no"]:
        return "YES" if index == 0 else "NO"
    return f"OUTCOME_{index}"


def _gamma_winner(outcome_prices: list[Any]) -> int | str | None:
    """Index of the winning outcome from final `outcomePrices`, 'INVALID' for an even split."""
    try:
        prices = [float(p) for p in outcome_prices]
    except (TypeError, ValueError):
        return None
    if not prices:
        return None
    if all(abs(p - 1 / len(prices)) < 1e-9 for p in prices):
        return "INVALID"
    winners = [i for i, p in enumerate(prices) if abs(p - 1) < 1e-9]
    losers = [i for i, p in enumerate(prices) if abs(p) < 1e-9]
    if len(winners) == 1 and len(losers) == len(prices) - 1:
        return winners[0]
    return None


def _chain_winner(v2_row: dict[str, Any], n_outcomes: int) -> int | str | None:
    payouts = v2_row.get("payouts")
    if payouts is not None:
        try:
            values = [int(v) for v in payouts]
        except (TypeError, ValueError):
            return None
        if len(values) != n_outcomes or sum(values) <= 0:
            return None
        if len(set(values)) == 1:
            return "INVALID"
        winners = [i for i, v in enumerate(values) if v == max(values)]
        if len(winners) == 1 and all(v == 0 for i, v in enumerate(values) if i != winners[0]):
            return winners[0]
        return None
    raw_price = v2_row.get("price")
    if raw_price is None or n_outcomes != 2:
        return None
    try:
        price = int(raw_price)
    except ValueError:
        return None
    if price == ONE_E18:
        return 0
    if price == 0:
        return 1
    if price == HALF_E18:
        return "INVALID"
    return None


def resolve_market(gamma: dict[str, Any], v2_row: dict[str, Any] | None) -> Resolution:
    """Combine Gamma's final outcome prices with the on-chain /v2/resolutions row.

    `resolved_at` comes only from /v2/resolutions (Gamma's `closedTime` is not the resolution
    time). If the two sources disagree about the outcome, the market is left UNRESOLVED and the
    disagreement is reported. A wrong settlement is worse than a missing one.
    """
    outcomes = [str(o) for o in json_list(gamma.get("outcomes"))]
    gamma_winner = _gamma_winner(json_list(gamma.get("outcomePrices")))

    if not v2_row or v2_row.get("status") != "resolved":
        return Resolution("UNRESOLVED", None)

    # Two row shapes (docs/DATA_NOTES.md): UMA rows carry `price` (1e18 fixed point, outcome 0);
    # native V2 / CTF rows carry `payouts` (micro-USDC per outcome) and `resolved_at`.
    resolved_at = parse_ts(v2_row.get("resolved_at")) or parse_ts(
        v2_row.get("last_update_timestamp")
    )
    chain_winner = _chain_winner(v2_row, len(outcomes))

    if chain_winner is None:
        return Resolution(
            "UNRESOLVED",
            None,
            f"unrecognised chain payout (price={v2_row.get('price')!r}, "
            f"payouts={v2_row.get('payouts')!r})",
        )
    if gamma_winner is None:
        return Resolution(
            "UNRESOLVED", None, f"gamma outcomePrices {gamma.get('outcomePrices')!r} not final"
        )
    if chain_winner != gamma_winner:
        return Resolution(
            "UNRESOLVED",
            None,
            f"gamma outcome {gamma_winner!r} disagrees with chain outcome {chain_winner!r}",
        )
    winner = chain_winner
    if winner == "INVALID":
        return Resolution("INVALID", resolved_at)
    assert isinstance(winner, int)
    return Resolution(_label(outcomes, winner), resolved_at)


def print_key(trade: dict[str, Any]) -> str:
    """Dedup key for a /v2/trades row. The API exposes no fill id (docs/DATA_NOTES.md)."""
    parts = [
        str(trade["transaction_hash"]),
        str(trade["token_id"]),
        str(trade.get("proxy_wallet", "")),
        str(trade["side"]),
        repr(float(trade["price"])),
        repr(float(trade["size"])),
        str(int(trade["timestamp"])),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def open_ts(gamma: dict[str, Any]) -> datetime | None:
    """First tradable moment: when the book started accepting orders, else the start date."""
    return parse_ts(gamma.get("acceptingOrdersTimestamp")) or parse_ts(gamma.get("startDate"))
