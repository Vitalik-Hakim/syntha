"""Closed vocabularies fixed by the PRD. Section references are to docs/PRD.md."""

from enum import StrEnum


class Side(StrEnum):
    """§3.3 — there is no shorting; short YES is expressed as BUY_NO. EITHER makes both tokens of
    each market candidates, so the entry rules choose the side (a price ≥ 0.9 rule buys the
    favourite, whichever outcome that is); a market never holds both sides at once."""

    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    EITHER = "EITHER"


class FillStatus(StrEnum):
    """§7 bt_trades.fill_status, plus the v3 §3 refusals."""

    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    UNEXECUTABLE = "UNEXECUTABLE"
    REJECTED_MIN_SIZE = "REJECTED_MIN_SIZE"
    UNEXECUTABLE_LIMIT_EXPIRED = (
        "UNEXECUTABLE_LIMIT_EXPIRED"  # v3 E1: no fill at the limit within the TTL
    )
    UNEXECUTABLE_BASKET_LEG = "UNEXECUTABLE_BASKET_LEG"  # v3 E3: a leg refused, nothing bought


EXECUTED_STATUSES = frozenset({FillStatus.FILLED.value, FillStatus.PARTIAL.value})


class ExitReason(StrEnum):
    """§12.4 — every closed trade has exactly one."""

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    MAX_HOLD = "MAX_HOLD"
    PRICE_TARGET = "PRICE_TARGET"
    RULE_EXIT = "RULE_EXIT"
    MARKET_RESOLVED = "MARKET_RESOLVED"
    END_OF_BACKTEST = "END_OF_BACKTEST"
    FORCED_EXPOSURE_LIMIT = "FORCED_EXPOSURE_LIMIT"
    DATA_GAP = "DATA_GAP"
    WALLET_SELL = "WALLET_SELL"  # v3 E4: the wallet whose print triggered the entry sold


class LiquiditySide(StrEnum):
    """v3 E2 — which taker prints count as liquidity for a fill."""

    SAME_SIDE = "SAME_SIDE"  # buys consume what other takers bought; sells, what they sold
    BOTH = "BOTH"  # v2 behaviour: every print in the bar; optimistic


class OrderType(StrEnum):
    """v3 E1."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class RunStatus(StrEnum):
    """§7 backtest_runs.status."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ExecutionModel(StrEnum):
    """§11 — BOOK is v2 and only valid where recorded L2 data covers the period."""

    LAST_PRICE = "LAST_PRICE"
    TRADE_PRINT = "TRADE_PRINT"
    BOOK = "BOOK"


class PartialFillPolicy(StrEnum):
    """§11.3."""

    ALLOW = "ALLOW"
    REJECT = "REJECT"


class FeeMode(StrEnum):
    """§3.7."""

    HISTORICAL = "HISTORICAL"
    CURRENT = "CURRENT"
    FIXED = "FIXED"


class SizingMode(StrEnum):
    """§10.3."""

    FIXED_CASH = "FIXED_CASH"
    PCT_OF_EQUITY = "PCT_OF_EQUITY"
    PCT_OF_INITIAL = "PCT_OF_INITIAL"
    FIXED_SHARES = "FIXED_SHARES"
