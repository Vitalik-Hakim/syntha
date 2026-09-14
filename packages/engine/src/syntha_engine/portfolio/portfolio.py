"""Cash ledger, positions, sizing, constraints and settlement (PRD §10).

All money is Decimal. Every cash movement is a ledger row. Invariants are asserted after every
bar when `check_invariants` is on, and a violation fails the run (PRD §10.2); nothing is clamped.

Exact closed-form check used here (equivalent to PRD §10.2's, but exact in Decimal):
    cash == starting_capital − Σ entry_cost(all positions) + Σ exit_net(all positions)
where entry_cost = shares × entry_price + entry_fee and exit_net = exit proceeds − exit fees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

from syntha_contracts.enums import ExitReason, FillStatus, SizingMode
from syntha_contracts.strategy import Constraints, Sizing
from syntha_engine.execution.fees import FeeTerms, fee_amount

ZERO = Decimal(0)


class InvariantViolation(AssertionError):
    """A portfolio invariant failed. The run is invalid and must not be reported."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    ts: int
    type: str  # DEPOSIT | BUY | SELL | FEE | SETTLEMENT
    amount: Decimal
    running_cash: Decimal
    ref: str


@dataclass
class Position:
    seq: int
    token_id: str
    condition_id: str
    event_id: str | None
    outcome_index: int
    side: str  # BUY_YES | BUY_NO
    signal_ts: int
    entry_ts: int
    entry_price: Decimal
    entry_ref_price: Decimal
    initial_shares: Decimal
    entry_fee: Decimal
    fee_terms: FeeTerms
    entry_trace: dict[str, Any]
    fill_status: FillStatus
    shares: Decimal = ZERO  # still open
    exit_value: Decimal = ZERO  # Σ shares sold × fill price (or payout)
    exit_ref_value: Decimal = ZERO  # Σ shares sold × reference price
    exit_fees: Decimal = ZERO
    exit_ts: int | None = None
    exit_reason: ExitReason | None = None
    exit_fills: list[dict[str, Any]] = field(default_factory=list)
    exit_trace: dict[str, Any] | None = None
    flags: dict[str, Any] = field(default_factory=dict)

    @property
    def entry_cost(self) -> Decimal:
        return self.initial_shares * self.entry_price + self.entry_fee

    @property
    def exit_net(self) -> Decimal:
        return self.exit_value - self.exit_fees

    @property
    def is_open(self) -> bool:
        return self.shares > ZERO


@dataclass(frozen=True, slots=True)
class SizingResult:
    shares: Decimal
    requested_notional: Decimal
    final_notional: Decimal
    binding: str | None
    liquidity_limited: bool
    rounded_up_to_min: bool = False
    # why the order is below the market minimum, when it is: sizing | cash |
    # max_total_exposure_pct | max_notional_per_trade | liquidity
    below_min_cause: str | None = None
    # the limit that would stop placing the minimum instead (None: rounding up would have filled)
    round_up_blocked_by: str | None = None


def _floor(x: Decimal, quantum: Decimal) -> Decimal:
    return x.quantize(quantum, rounding=ROUND_DOWN) if x > 0 else ZERO


def size_entry(
    sizing: Sizing,
    *,
    price: Decimal,
    equity: Decimal,
    starting_capital: Decimal,
    cash: Decimal,
    exposure_headroom: Decimal,
    liquidity_cap: Decimal | None,
    fee: FeeTerms,
    share_precision: int,
    min_order_size: Decimal | None = None,
) -> SizingResult:
    """Shares to buy after every clamp (PRD §10.3). `binding` names the constraint that set the
    final size, first in order on ties: max_notional_per_trade, cash, max_total_exposure_pct,
    liquidity."""
    q = Decimal(1).scaleb(-share_precision)
    value = Decimal(str(sizing.value))
    if sizing.mode == SizingMode.FIXED_SHARES:
        shares = _floor(value, q)
        requested = value * price
    else:
        requested = {
            SizingMode.FIXED_CASH: value,
            SizingMode.PCT_OF_EQUITY: value * equity,
            SizingMode.PCT_OF_INITIAL: value * starting_capital,
        }[sizing.mode]
        shares = _floor(requested / price, q)

    binding: str | None = None
    per_share = price
    if fee.rate > ZERO:
        per_share += fee.rate * (price * (1 - price)) ** fee.exponent
    limits: list[tuple[str, Decimal | None]] = [
        (
            "max_notional_per_trade",
            Decimal(str(sizing.max_notional_per_trade)) if sizing.max_notional_per_trade else None,
        ),
        ("cash", cash),
        ("max_total_exposure_pct", exposure_headroom),
    ]
    for name, limit in limits:
        if limit is None:
            continue
        per = per_share if name == "cash" else price
        cap = _floor(limit / per, q)
        if cap < shares:
            shares, binding = cap, name

    liquidity_limited = False
    if liquidity_cap is not None:
        cap = _floor(liquidity_cap, q)
        if cap < shares:
            shares, binding, liquidity_limited = cap, "liquidity", True

    while shares > ZERO and shares * price + fee_amount(shares, price, fee) > cash:
        shares -= q  # fee rounding can exceed the analytic bound by a quantum
        binding = "cash"

    if min_order_size is None or shares >= min_order_size:
        return SizingResult(shares, requested, shares * price, binding, liquidity_limited)

    # Below the market minimum. ROUND_UP places the minimum when every hard limit still allows
    # it; otherwise (or with SKIP) the order is rejected and the cause is recorded.
    target = min_order_size.quantize(q, rounding=ROUND_UP)
    notional = target * price
    checks = [
        ("max_notional_per_trade", limits[0][1] is None or notional <= limits[0][1]),
        ("cash", notional + fee_amount(target, price, fee) <= cash),
        ("max_total_exposure_pct", notional <= exposure_headroom),
        ("liquidity", liquidity_cap is None or target <= liquidity_cap),
        ("sizing", notional <= requested * Decimal(str(sizing.round_up_max_multiple))),
    ]
    failed = next((name for name, ok in checks if not ok), None)
    if sizing.min_order_policy == "ROUND_UP" and failed is None:
        return SizingResult(
            target, requested, notional, "min_order_size", False, rounded_up_to_min=True
        )
    return SizingResult(
        shares,
        requested,
        shares * price,
        binding,
        liquidity_limited,
        below_min_cause=binding if binding is not None else "sizing",
        round_up_blocked_by=failed,
    )


class Portfolio:
    def __init__(self, starting_capital: Decimal, start_ts: int, *, check_invariants: bool) -> None:
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.check = check_invariants
        self.ledger: list[LedgerEntry] = [
            LedgerEntry(start_ts, "DEPOSIT", starting_capital, starting_capital, "starting_capital")
        ]
        self.open: dict[str, Position] = {}  # token → position
        self.all_positions: list[Position] = []
        self.rejected_rows: list[dict[str, Any]] = []
        self.realized_pnl = ZERO
        self._closed_net = ZERO  # Σ (exit_net − entry_cost) over closed positions
        self.last_exit_ts: dict[str, int] = {}  # condition → ts
        self.entered_conditions: set[str] = set()
        self._seq = 0

    # ------------------------------------------------------------ bookkeeping

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _move(self, ts: int, kind: str, amount: Decimal, ref: str) -> None:
        if amount == ZERO:
            return
        self.cash += amount
        self.ledger.append(LedgerEntry(ts, kind, amount, self.cash, ref))

    def positions_value(self, marks: dict[str, Decimal]) -> Decimal:
        return sum((p.shares * marks[t] for t, p in self.open.items()), ZERO)

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        return self.cash + self.positions_value(marks)

    # ------------------------------------------------------------ constraints

    def can_open(
        self,
        token_id: str,
        condition_id: str,
        event_id: str | None,
        ts: int,
        constraints: Constraints,
        pending_tokens: set[str],
        pending_events: list[str | None],
    ) -> str | None:
        """None if a new position may be signalled, else the blocking constraint."""
        if token_id in self.open or token_id in pending_tokens:
            return "already_holding"
        if len(self.open) + len(pending_tokens) >= constraints.max_open_positions:
            return "max_open_positions"
        if event_id is not None:
            in_event = sum(1 for p in self.open.values() if p.event_id == event_id)
            in_event += sum(1 for e in pending_events if e == event_id)
            if in_event >= constraints.max_positions_per_event:
                return "max_positions_per_event"
        if not constraints.allow_reentry_same_market and condition_id in self.entered_conditions:
            return "reentry_not_allowed"
        last = self.last_exit_ts.get(condition_id)
        if last is not None and ts < last + constraints.cooldown_minutes_per_market * 60:
            return "cooldown"
        return None

    # ------------------------------------------------------------ trades

    def open_position(self, pos: Position) -> None:
        cost = pos.initial_shares * pos.entry_price
        self._move(pos.entry_ts, "BUY", -cost, f"seq:{pos.seq}")
        self._move(pos.entry_ts, "FEE", -pos.entry_fee, f"seq:{pos.seq}")
        pos.shares = pos.initial_shares
        self.open[pos.token_id] = pos
        self.all_positions.append(pos)
        self.entered_conditions.add(pos.condition_id)

    def reduce(
        self,
        pos: Position,
        ts: int,
        shares: Decimal,
        price: Decimal,
        ref_price: Decimal,
        fee: Decimal,
        fill: dict[str, Any],
    ) -> None:
        self._move(ts, "SELL", shares * price, f"seq:{pos.seq}")
        self._move(ts, "FEE", -fee, f"seq:{pos.seq}")
        pos.shares -= shares
        pos.exit_value += shares * price
        pos.exit_ref_value += shares * ref_price
        pos.exit_fees += fee
        pos.exit_fills.append(fill)

    def close(self, pos: Position, ts: int, reason: ExitReason, trace: dict[str, Any]) -> None:
        pos.exit_ts, pos.exit_reason, pos.exit_trace = ts, reason, trace
        del self.open[pos.token_id]
        self.last_exit_ts[pos.condition_id] = ts
        self.realized_pnl += pos.exit_net - pos.entry_cost
        self._closed_net += pos.exit_net - pos.entry_cost

    def settle(
        self, pos: Position, ts: int, payout: Decimal, fee: Decimal, trace: dict[str, Any]
    ) -> None:
        shares = pos.shares
        self._move(ts, "SETTLEMENT", shares * payout, f"seq:{pos.seq}")
        self._move(ts, "FEE", -fee, f"seq:{pos.seq}")
        pos.shares = ZERO
        pos.exit_value += shares * payout
        pos.exit_ref_value += shares * payout
        pos.exit_fees += fee
        pos.exit_fills.append({"type": "SETTLEMENT", "shares": str(shares), "payout": str(payout)})
        self.close(pos, ts, ExitReason.MARKET_RESOLVED, trace)

    def value_at_mark(self, pos: Position, ts: int, mark: Decimal, trace: dict[str, Any]) -> None:
        """End of backtest: book the position at its mark. No fill is simulated and no cost charged."""
        shares = pos.shares
        self._move(ts, "SELL", shares * mark, f"seq:{pos.seq}:mark")
        pos.shares = ZERO
        pos.exit_value += shares * mark
        pos.exit_ref_value += shares * mark
        pos.exit_fills.append({"type": "MARK", "shares": str(shares), "price": str(mark)})
        self.close(pos, ts, ExitReason.END_OF_BACKTEST, trace)

    # ------------------------------------------------------------ invariants

    def assert_invariants(self, marks: dict[str, Decimal], max_open: int) -> None:
        if not self.check:
            return
        if self.cash < ZERO:
            raise InvariantViolation(f"cash went negative: {self.cash}")
        expected = self.starting_capital + self._closed_net
        for p in self.open.values():
            expected += p.exit_net - p.entry_cost
        if expected != self.cash:
            raise InvariantViolation(f"cash {self.cash} != closed-form {expected}")
        if self.ledger[-1].running_cash != self.cash:
            raise InvariantViolation("ledger running cash does not match cash")
        if len(self.open) > max_open:
            raise InvariantViolation(f"{len(self.open)} open positions exceeds {max_open}")
        for t, p in self.open.items():
            if t not in marks:
                raise InvariantViolation(f"open position {t} has no mark")
            if p.shares <= ZERO:
                raise InvariantViolation(f"open position {t} has {p.shares} shares")
