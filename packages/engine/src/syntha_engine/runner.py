"""The event loop (PRD §9): one global chronological pass, one cash balance, one clock.

Per bar k, in an order that must not change (PRD §9.1):
  1. mark open positions to market
  2. settle positions whose market resolved in (bar k−1, bar k]
  3. evaluate exits (before entries, so freed cash is usable this bar)
  4. evaluate entries for candidate tokens
  5. execute signals whose decision lag has elapsed: exits first, then entries
  6. snapshot equity and assert invariants

Look-ahead discipline: strategies only ever receive a MarketView. Resolution data
(`SettlementTable`) is used only to settle positions and to stop trading in markets that have
already closed. That is the market's reality, not information handed to the strategy.
"""

from __future__ import annotations

import copy
import math
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

import numpy as np

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.enums import ExitReason, FillStatus, PartialFillPolicy, Side, SizingMode
from syntha_engine import ENGINE_VERSION
from syntha_engine import nearmiss as NM
from syntha_engine.data import Dataset, FeeScheduleEntry, SettlementTable
from syntha_engine.execution import fills
from syntha_engine.execution.fees import fee_amount, resolve_fee_terms
from syntha_engine.grid import Grid, TokenFrame, build_frame
from syntha_engine.metrics.core import EquityPoint, compute_metrics
from syntha_engine.portfolio.portfolio import ZERO, Portfolio, Position, size_entry
from syntha_engine.strategy import features as F
from syntha_engine.strategy.compiler import (
    PRINT_TRIGGER,
    RANDOM_DRAW,
    BasketDecision,
    DSLStrategy,
    Strategy,
    compile_strategy,
)
from syntha_engine.strategy.view import (
    Bar,
    BarWindow,
    EventView,
    FeatureView,
    MarketStatic,
    MarketView,
    PositionView,
    SiblingView,
)


class EngineInconsistency(RuntimeError):
    """The vectorized and look-ahead-safe evaluation paths disagreed. The run is invalid."""


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def _iso(ts: int | None) -> str | None:
    return None if ts is None else _dt(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _from_iso(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _q6(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.000001")))


def _q6_or_none(x: Decimal | None) -> str | None:
    return None if x is None else _q6(x)


def _with_windows(clauses: list[dict[str, Any]], close_ts: int) -> list[dict[str, Any]]:
    """Attach the time window each lookback feature read: it ends at the signal bar's close."""
    out = []
    for c in clauses:
        params = c.get("params") or {}
        minutes = max(
            (v for v in params.values() if isinstance(v, int | float) and not isinstance(v, bool)),
            default=0,
        )
        if minutes and c.get("feature") != PRINT_TRIGGER:
            c = {**c, "window": {"from": _iso(close_ts - int(minutes * 60)), "to": _iso(close_ts)}}
        out.append(c)
    return out


@dataclass(slots=True)
class EquityRow:
    ts: int
    cash: Decimal
    positions_value: Decimal
    open_positions: int
    realized_pnl: Decimal


@dataclass
class Signal:
    kind: str  # ENTRY | EXIT | BASKET
    token_id: str
    signal_k: int
    due_k: int
    trace: dict[str, Any] | None  # entry traces of DSL strategies are built when recorded
    reason: ExitReason | None = None
    # v3 E1: a resting order. `limit` None means a MARKET order; the order is live through
    # `expires_k` and records the best same-side price it saw while waiting.
    limit: Decimal | None = None
    expires_k: int | None = None
    best_seen: float | None = None
    wallet: int | None = None  # v3 E4: the wallet whose print triggered the entry
    basket: Any = None  # v3 E3: BasketDecision for kind BASKET


# Refusals an order can wait out within its TTL (v3 E1); everything else fails at once.
WAITABLE_REFUSALS = frozenset(
    {fills.NO_PRINTS, fills.NO_PRINTS_AT_LIMIT, fills.NO_PRICE, fills.STALE_PRICE}
)
RESTING = "RESTING"
LIMIT_EXPIRED = "LIMIT_EXPIRED"


def _limit_for(frame: TokenFrame, k: int, direction: str, order: Any) -> Decimal | None:
    """The limit price an order created at bar k carries (v3 E1): the bar's price plus (buys) or
    minus (sells) `limit_offset_ticks`, clamped to the price range. None for MARKET orders or
    when the bar has no price."""
    if order.type != "LIMIT":
        return None
    price = frame.price_at(k)
    if math.isnan(price):
        return None
    tick = frame.terms.tick_size
    sign = 1 if direction == "BUY" else -1
    limit = fills.to_decimal(price) + sign * order.limit_offset_ticks * tick
    return Decimal(min(max(limit, tick), 1 - tick))


# A run whose signals were this often unexecutable or rejected is PARTIAL: not an error, not a
# clean success (PRD §19).
PARTIAL_EXECUTION_SHARE = 0.25

# Refusals that cannot clear while the market exists: no further entry attempts on the token.
PERMANENT_REFUSALS = frozenset({fills.MIN_SIZE_UNKNOWN, fills.MARKET_CLOSED, fills.END_OF_DATA})


@dataclass
class _Episode:
    """One entry signal: from the bar the entry condition starts holding until it fills, stops
    holding, or becomes impossible. Failed attempts inside an episode are retried on later bars
    and reported as a single trade row with the attempt count (PRD §11.3, §16)."""

    start_k: int
    last_candidate_k: int
    attempts: int = 0
    attempt_pending: bool = False
    last_row: dict[str, Any] | None = None
    reasons: Counter[str] = field(default_factory=Counter)


@dataclass
class BacktestResult:
    manifest: dict[str, Any]
    trades: list[dict[str, Any]]
    equity: list[dict[str, Any]]
    ledger: list[dict[str, Any]]
    metrics: dict[str, Any]
    data_quality: dict[str, Any]
    near_misses: list[dict[str, Any]] = field(default_factory=list)
    # (start bar, last candidate bar, token) of every entry signal episode, for the null tests
    episodes: list[tuple[int, int, str]] = field(default_factory=list)


@dataclass(frozen=True)
class ForcedSignals:
    """Entry candidates imposed instead of the strategy's own (PRD §15.3 null models). Each
    (bar, token index into `Preparation.token_names`) is a bar at which an entry is attempted,
    subject to the same constraints, sizing, execution and exits."""

    ev_k: np.ndarray
    ev_tok: np.ndarray
    label: str


@dataclass(frozen=True)
class LeanOutcome:
    """What a null-model simulation reports: no traces, trade rows or equity curve."""

    ending_equity: Decimal
    net_profit: Decimal
    roi: float
    n_trades: int
    signals_generated: int
    signals_filled: int


@dataclass
class _Prepared:
    frame: TokenFrame
    features: dict[str, np.ndarray]
    draws: np.ndarray | None
    block_local: int  # local index from which trading is closed (resolved or closed)
    candidates: np.ndarray  # local indices
    near: list[tuple[str, int, float | None]] = field(
        default_factory=list
    )  # (kind, local i, score)
    gate: np.ndarray | None = None  # eligible bars (universe filters passed), local
    trigger_wallet: np.ndarray | None = None  # v3 E4: wallet id of the qualifying print per bar


@dataclass
class _Quality:
    counters: Counter[str] = field(default_factory=Counter)
    unexecutable: Counter[str] = field(default_factory=Counter)
    exit_unexecutable: Counter[str] = field(default_factory=Counter)
    attempts_failed: Counter[str] = field(default_factory=Counter)
    blocked_by_constraint: Counter[str] = field(default_factory=Counter)
    fee_labels: set[str] = field(default_factory=set)
    fee_warnings: Counter[str] = field(default_factory=Counter)
    markets_dropped: list[dict[str, str]] = field(default_factory=list)
    entry_funnel: Counter[str] = field(default_factory=Counter)
    near_misses: dict[str, Any] = field(default_factory=dict)
    min_size_causes: Counter[str] = field(default_factory=Counter)
    episodes: list[tuple[int, int, str]] = field(default_factory=list)


def _tokens_for_side(dataset: Dataset, condition_id: str, side: Side) -> list[str]:
    wanted = {Side.BUY_YES: (0,), Side.BUY_NO: (1,), Side.EITHER: (0, 1)}[side]
    return [
        t.clob_token_id
        for t in dataset.tokens.values()
        if t.condition_id == condition_id and t.outcome_index in wanted
    ]


def _side_of(outcome_index: int) -> str:
    return Side.BUY_YES.value if outcome_index == 0 else Side.BUY_NO.value


def _print_trigger(
    frame: TokenFrame, side: str, min_notional: float, allowed: set[int] | None
) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: the largest qualifying print's notional (NaN when none) and its wallet id (v3 E4).
    Prints are known at the bar's close, like every other bar feature."""
    notional = np.full(frame.n, np.nan)
    wallet = np.full(frame.n, -1, dtype=np.int32)
    value = frame.raw_price * frame.raw_size
    mask = (frame.raw_side == (1 if side == "BUY" else -1)) & (value >= min_notional)
    if allowed is not None:
        mask &= np.isin(frame.raw_wallet, np.fromiter(allowed, dtype=np.int32, count=len(allowed)))
    for j in np.flatnonzero(mask):
        i = int(frame.raw_local[j])
        if np.isnan(notional[i]) or value[j] > notional[i]:
            notional[i] = value[j]
            wallet[i] = frame.raw_wallet[j]
    return notional, wallet


@dataclass
class Preparation:
    """Everything computed once per (request, dataset): frames, features, eligibility gates and the
    strategy's own entry candidates. Several simulations can share one (PRD §15.3)."""

    request: BacktestRequest
    dataset: Dataset
    settlement: SettlementTable
    strat: Strategy
    grid: Grid
    prepared: dict[str, _Prepared]
    token_names: list[str]
    ev_k: np.ndarray
    ev_tok: np.ndarray
    settle_at: dict[int, set[str]]
    funnel: Counter[str]
    quality: _Quality
    n_wanted: int
    near_misses_enabled: bool
    near_miss_separation: int
    # v3 E3: sibling tokens per event, and the bars at which a set identity could be violated
    event_tokens: dict[str, list[str]] = field(default_factory=dict)
    basket_events: dict[str, np.ndarray] = field(default_factory=dict)
    basket_k: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))


def run(
    request: BacktestRequest,
    dataset: Dataset,
    settlement: SettlementTable,
    *,
    strategy: Strategy | None = None,
    progress: Callable[[str, float], None] | None = None,
) -> BacktestResult:
    """Run a backtest. `progress(phase, fraction)` is called occasionally (phases: prepare, simulate)."""
    prep = prepare(request, dataset, settlement, strategy=strategy, progress=progress)
    result = simulate(prep, progress=progress)
    assert isinstance(result, BacktestResult)
    return result


def prepare(
    request: BacktestRequest,
    dataset: Dataset,
    settlement: SettlementTable,
    *,
    strategy: Strategy | None = None,
    progress: Callable[[str, float], None] | None = None,
) -> Preparation:
    report = progress or (lambda phase, fraction: None)
    step_min = request.bar_interval_minutes
    strat: Strategy = strategy or compile_strategy(request.strategy, step_min)
    universe = request.strategy.universe
    q = _Quality()

    # ------------------------------------------------------------ universe (static part)
    wanted = sorted(dataset.markets)
    if request.dataset.condition_ids is not None:
        wanted = [c for c in wanted if c in set(request.dataset.condition_ids)]
    if universe.categories is not None:
        cats = {c.lower() for c in universe.categories}
        wanted = [c for c in wanted if (dataset.markets[c].category or "").lower() in cats]
    tokens: list[str] = []
    for cid in wanted:
        found = _tokens_for_side(dataset, cid, universe.side)
        if not found:
            q.markets_dropped.append({"condition_id": cid, "reason": "NO_TOKEN_FOR_SIDE"})
        tokens.extend(found)
    tokens.sort()

    firsts = [
        int(a[0])
        for t in tokens
        for a in (dataset.tokens[t].bar_ts, dataset.tokens[t].print_ts)
        if a.size
    ]
    lasts = [
        int(a[-1])
        for t in tokens
        for a in (dataset.tokens[t].bar_ts, dataset.tokens[t].print_ts)
        if a.size
    ]
    start_ts = (
        int(request.dataset.start.timestamp())
        if request.dataset.start
        else (min(firsts) if firsts else 0)
    )
    if request.dataset.end:
        end_ts = int(request.dataset.end.timestamp())
    else:
        # Without an explicit end, run until the last selected market resolves, so positions
        # settle instead of being marked. Prices stop shortly after trading closes, but
        # resolution can come hours or days later. This only sets the length of the clock;
        # strategies never see resolution times.
        resolutions = [
            s.resolved_ts
            for t in tokens
            if (s := settlement.get(dataset.tokens[t].condition_id)).resolved_ts is not None
        ]
        end_ts = max(lasts + resolutions) if lasts else start_ts
    grid = Grid.build(start_ts, max(end_ts, start_ts), step_min)

    # ------------------------------------------------------------ precompute (vectorized)
    prepared: dict[str, _Prepared] = {}
    near_misses_enabled = (
        isinstance(strat, DSLStrategy)
        and strat.dsl.entry.random is None
        and not strat.uses_print_trigger
        and not strat.uses_basket
    )
    trigger_allowed: set[int] | None = None
    trigger = strat.dsl.entry.print_trigger if isinstance(strat, DSLStrategy) else None
    if trigger is not None and trigger.wallets is not None:
        wanted_wallets = set(trigger.wallets)
        trigger_allowed = {i for i, w in enumerate(dataset.wallets) if w in wanted_wallets}
    near_miss_separation = max(strat.lookback_bars, 60 // step_min, 1)
    funnel: Counter[str] = Counter()
    ev_k_parts: list[np.ndarray] = []
    ev_tok_parts: list[np.ndarray] = []
    token_names: list[str] = []
    settle_at: dict[int, set[str]] = {}
    for i_tok, tok in enumerate(tokens):
        if i_tok % 25 == 0:
            report("prepare", i_tok / max(len(tokens), 1))
        td = dataset.tokens[tok]
        terms = dataset.markets[td.condition_id]
        frame = build_frame(td, terms, grid)
        if frame is None:
            q.markets_dropped.append(
                {"condition_id": td.condition_id, "reason": "NO_PRICE_HISTORY"}
            )
            continue
        feats = {
            ref.key: F.compute(frame, ref.name, dict(ref.params)) for ref in strat.feature_refs
        }
        trigger_wallet = None
        if trigger is not None:
            feats[PRINT_TRIGGER], trigger_wallet = _print_trigger(
                frame, trigger.side, trigger.min_notional, trigger_allowed
            )
        draws = None
        if strat.uses_random:
            rng = np.random.default_rng([request.seed & 0xFFFFFFFF, zlib.crc32(tok.encode())])
            draws = rng.random(frame.n)

        s = settlement.get(td.condition_id)
        close_ts = grid.start + (frame.k0 + np.arange(frame.n) + 1) * grid.step
        halt = min(x for x in (s.resolved_ts, s.closed_ts, 2**62) if x is not None)
        not_halted = close_ts < halt
        block_local = int(np.argmin(not_halted)) if not bool(np.all(not_halted)) else frame.n
        if s.resolved_ts is not None:
            sk = grid.first_bar_at_or_after(s.resolved_ts)
            if 0 <= sk < grid.n:
                settle_at.setdefault(sk, set()).add(td.condition_id)

        # Eligibility gates in funnel order; each count is bars lost at that step.
        gate = ~np.isnan(frame.price)
        funnel["no_price_yet"] += int(frame.n - gate.sum())
        stages: list[tuple[str, np.ndarray]] = [("trading_halted", not_halted)]
        if terms.open_ts is not None:
            stages.append(("before_market_open", close_ts >= terms.open_ts))
        if universe.min_volume_asof is not None:
            stages.append(
                ("below_min_volume_asof", frame.cum_notional() >= universe.min_volume_asof)
            )
        if universe.min_time_to_resolution_minutes is not None:
            ttr_ok = (
                np.zeros(frame.n, dtype=bool)
                if terms.end_ts is None
                else (terms.end_ts - close_ts) / 60 >= universe.min_time_to_resolution_minutes
            )
            stages.append(("below_min_time_to_resolution", ttr_ok))
        staleness = F.staleness_minutes(frame, {})
        stages.append(("stale_price", staleness <= request.max_stale_minutes))
        for name, ok in stages:
            funnel[name] += int(np.sum(gate & ~ok))
            if name == "stale_price":
                q.counters["bars_blocked_stale"] += int(np.sum(gate & ~ok))
            gate &= ok
        funnel["eligible"] += int(gate.sum())
        q.counters["bars_total"] += frame.n
        q.counters["bars_with_no_prints"] += frame.n - int(frame.pr_local.size)
        q.counters["prints_total"] += frame.n_prints

        near: list[tuple[str, int, float | None]] = []
        if isinstance(strat, DSLStrategy):
            mask, nulls = strat.entry_mask(feats, draws, frame.n)
            q.counters["bars_skipped_null_feature"] += int(np.sum(gate & nulls))
            funnel["entry_rules_true_ignoring_universe"] += int(mask.sum())
            candidates = np.flatnonzero(gate & mask)
            funnel["entry_rules_true_when_eligible"] += int(candidates.size)
            closeness = strat.entry_closeness(feats)
            if closeness is not None:
                for i in NM.nearest(
                    closeness,
                    np.flatnonzero(gate & ~mask),
                    NM.PER_MARKET[NM.RULES_FAILED],
                    near_miss_separation,
                ):
                    near.append((NM.RULES_FAILED, i, float(closeness[i])))
                gated_out = mask & ~gate & ~np.isnan(frame.price) & not_halted
                for i in NM.stretch_starts(gated_out, NM.PER_MARKET[NM.BLOCKED_BY_UNIVERSE]):
                    near.append((NM.BLOCKED_BY_UNIVERSE, i, None))
        else:
            candidates = np.flatnonzero(gate)
        prepared[tok] = _Prepared(
            frame, feats, draws, block_local, candidates, near, gate, trigger_wallet
        )
        if candidates.size:
            ev_k_parts.append(frame.k0 + candidates)
            ev_tok_parts.append(np.full(candidates.size, len(token_names), dtype=np.int32))
        token_names.append(tok)

    event_tokens: dict[str, list[str]] = {}
    basket_events: dict[str, np.ndarray] = {}
    basket_k = np.empty(0, np.int64)
    if isinstance(strat, DSLStrategy) and strat.uses_basket:
        for tok in token_names:
            ev_id = dataset.markets[dataset.tokens[tok].condition_id].event_id
            if ev_id is not None:
                event_tokens.setdefault(ev_id, []).append(tok)
        basket_events = _basket_candidates(prepared, event_tokens, strat.dsl.entry.basket)
        if basket_events:
            basket_k = np.unique(np.concatenate(list(basket_events.values())))

    # Candidate (bar, token) pairs sorted by bar, ties in token order (tokens are sorted).
    ev_k = np.concatenate(ev_k_parts) if ev_k_parts else np.empty(0, np.int64)
    ev_tok = np.concatenate(ev_tok_parts) if ev_tok_parts else np.empty(0, np.int32)
    order = np.argsort(ev_k, kind="stable")
    ev_k, ev_tok = ev_k[order], ev_tok[order]
    return Preparation(
        request=request,
        dataset=dataset,
        settlement=settlement,
        strat=strat,
        grid=grid,
        prepared=prepared,
        token_names=token_names,
        ev_k=ev_k,
        ev_tok=ev_tok,
        settle_at=settle_at,
        funnel=funnel,
        quality=q,
        n_wanted=len(wanted),
        near_misses_enabled=near_misses_enabled,
        near_miss_separation=near_miss_separation,
        event_tokens=event_tokens,
        basket_events=basket_events,
        basket_k=basket_k,
    )


def _basket_candidates(
    prepared: dict[str, _Prepared],
    event_tokens: dict[str, list[str]],
    basket: Any,
) -> dict[str, np.ndarray]:
    """Bars at which an event's open siblings *could* satisfy the basket rule, screened without
    fees or ticks (a superset of the exact check `evaluate_event` makes at those bars)."""
    out: dict[str, np.ndarray] = {}
    yes_set = basket.kind == "NEGRISK_YES_SET"
    for ev, toks in event_tokens.items():
        if len(toks) < basket.min_markets:
            continue
        if not all(prepared[t].frame.terms.neg_risk for t in toks):
            continue  # not a mutually exclusive set: no identity to violate
        frames = [prepared[t].frame for t in toks]
        k_lo = min(f.k0 for f in frames)
        k_hi = max(f.k0 + f.n for f in frames)
        width = k_hi - k_lo
        price_sum = np.zeros(width)
        n_open = np.zeros(width, dtype=np.int64)
        catch = np.zeros(width, dtype=bool)
        for t in toks:
            p = prepared[t]
            f = p.frame
            o = f.k0 - k_lo
            is_open = ~np.isnan(f.price) & (np.arange(f.n) < p.block_local)
            price_sum[o : o + f.n] += np.where(is_open, f.price, 0.0)
            n_open[o : o + f.n] += is_open
            if f.terms.is_catch_all:
                catch[o : o + f.n] |= is_open
        payoff = np.ones(width) if yes_set else (n_open - 1).astype(np.float64)
        cand = (n_open >= basket.min_markets) & (price_sum <= payoff - basket.min_edge + 1e-9)
        if basket.require_catch_all:
            cand &= catch
        if cand.any():
            out[ev] = k_lo + np.flatnonzero(cand)
    return out


def simulate(
    prep: Preparation,
    *,
    forced: ForcedSignals | None = None,
    near_misses: bool = True,
    progress: Callable[[str, float], None] | None = None,
) -> BacktestResult | LeanOutcome:
    """The event loop over a preparation. With `forced` entry candidates the run is lean: no
    rule traces, trade rows or equity curve, only the outcome (PRD §15.3 null models)."""
    report = progress or (lambda phase, fraction: None)
    request, dataset, settlement, strat, grid = (
        prep.request,
        prep.dataset,
        prep.settlement,
        prep.strat,
        prep.grid,
    )
    universe = request.strategy.universe
    constraints = request.strategy.constraints
    lag = request.decision_lag_bars
    prepared, token_names, settle_at, funnel = (
        prep.prepared,
        prep.token_names,
        prep.settle_at,
        prep.funnel,
    )
    q = copy.deepcopy(prep.quality)
    order = request.strategy.order
    watches_wallets = isinstance(strat, DSLStrategy) and any(
        r.type == "WALLET_SELL" for r in strat.dsl.exit.any
    )
    lean = forced is not None
    ev_k, ev_tok = (forced.ev_k, forced.ev_tok) if forced is not None else (prep.ev_k, prep.ev_tok)
    near_misses_enabled = prep.near_misses_enabled and near_misses and not lean
    near_miss_separation = prep.near_miss_separation
    dsl_only = isinstance(strat, DSLStrategy)
    uses_basket = isinstance(strat, DSLStrategy) and strat.uses_basket and not lean
    basket_k = prep.basket_k if uses_basket else np.empty(0, np.int64)
    n_baskets = 0
    basket_k_set = set(basket_k.tolist())

    def baskets_at(k: int) -> list[str]:
        return sorted(ev for ev, ks in prep.basket_events.items() if k in set(ks.tolist()))

    def event_view(ev: str, k: int) -> EventView:
        """The event's siblings open at bar k, point-in-time (v3 E3): listed by the bar's close,
        priced, not halted; catch-all status only from listed siblings."""
        sibs: list[SiblingView] = []
        catch = False
        close = grid.close(k)
        for tok in prep.event_tokens[ev]:
            p = prepared[tok]
            f = p.frame
            i = k - f.k0
            if not (0 <= i < p.block_local) or math.isnan(f.price[i]):
                continue
            if f.terms.open_ts is not None and f.terms.open_ts > close:
                continue
            vol, notional, _, _ = f.prints_at(k, 1)
            sibs.append(
                SiblingView(
                    f.terms.condition_id,
                    tok,
                    f.token.outcome_index,
                    float(f.price[i]),
                    float(f.terms.tick_size),
                    None if f.terms.min_order_size is None else float(f.terms.min_order_size),
                    bool(f.terms.is_catch_all),
                    vol,
                    (notional / vol) if vol > 0 else None,
                )
            )
            catch = catch or bool(f.terms.is_catch_all)
        return EventView(
            _dt(grid.ts(k)),
            ev,
            tuple(sibs),
            catch,
            bool(sibs) and all(bool(prepared[s.clob_token_id].frame.terms.neg_risk) for s in sibs),
        )

    def candidates_at(k: int) -> list[str]:
        lo, hi = np.searchsorted(ev_k, k, "left"), np.searchsorted(ev_k, k, "right")
        return [token_names[i] for i in ev_tok[lo:hi]]

    portfolio = Portfolio(
        request.starting_capital,
        grid.start,
        check_invariants=request.check_invariants and not lean,
    )
    pending: list[Signal] = []
    equity_rows: list[EquityRow] = []

    def view_for(tok: str, k: int, pos: Position | None, wallet_sold: bool = False) -> MarketView:
        p = prepared[tok]
        frame = p.frame
        i = k - frame.k0
        in_range = 0 <= i < frame.n
        price = frame.price_at(k)
        values: dict[str, float | None] = {}
        for key, arr in p.features.items():
            v = float(arr[i]) if in_range else math.nan
            values[key] = None if math.isnan(v) else v
        if p.draws is not None:
            values[RANDOM_DRAW] = float(p.draws[i]) if in_range else None
        last = min(i, frame.n - 1)
        # DSL strategies read only features, price and position, so they get no bar window;
        # copying one per view was the engine's largest cost.
        first = last + 1 if dsl_only else max(0, last - strat.lookback_bars + 1)
        window = tuple(
            Bar(
                _dt(grid.ts(frame.k0 + j)),
                None if math.isnan(frame.price[j]) else float(frame.price[j]),
                bool(frame.observed[j]),
                frame.prints_at(frame.k0 + j)[0],
                frame.print_count_at(j),
            )
            for j in range(first, last + 1)
        )
        terms = frame.terms
        static = MarketStatic(
            condition_id=terms.condition_id,
            clob_token_id=tok,
            outcome_index=frame.token.outcome_index,
            outcome_name=frame.token.outcome_name,
            category=terms.category,
            tick_size=float(terms.tick_size),
            min_order_size=None if terms.min_order_size is None else float(terms.min_order_size),
            open_ts=None if terms.open_ts is None else _dt(terms.open_ts),
        )
        pv = None
        if pos is not None:
            pv = PositionView(
                _dt(pos.entry_ts),
                float(pos.entry_price),
                float(pos.shares),
                (grid.ts(k) - pos.entry_ts) / 60,
                pos.signal_wallet,
            )
        return MarketView(
            _dt(grid.ts(k)),
            None if math.isnan(price) else price,
            BarWindow(window),
            FeatureView(values),
            static,
            pv,
            wallet_sold,
        )

    def marks() -> dict[str, Decimal]:
        return {t: fills.to_decimal(prepared[t].frame.price_at(k)) for t in portfolio.open}

    def halted(tok: str, k: int) -> bool:
        p = prepared[tok]
        return k - p.frame.k0 >= p.block_local

    def snapshot(k: int, m: dict[str, Decimal]) -> None:
        if lean:
            return
        equity_rows.append(
            EquityRow(
                grid.ts(k),
                portfolio.cash,
                portfolio.positions_value(m),
                len(portfolio.open),
                portfolio.realized_pnl,
            )
        )
        portfolio.assert_invariants(m, constraints.max_open_positions)

    episodes: dict[str, _Episode] = {}
    dead_tokens: set[str] = set()
    evaluate_exits = not (isinstance(strat, DSLStrategy) and not strat.dsl.exit.any)
    constraint_misses: dict[str, list[tuple[int, str]]] = {}
    notional_prefix: dict[str, np.ndarray] = {}  # small LRU: traces cluster by token
    last_blocked_k: dict[str, int] = {}

    capacity: dict[int, tuple[Decimal, Decimal]] = {}

    def capacity_block(tok: str, k: int, min_order_size: Decimal) -> str | None:
        """A new signal needs room for at least the market's minimum order: otherwise the
        portfolio is full, which is a constraint, not a missed trade. Uses bar-k marks and cash
        before this bar's executions; the order is sized again when it executes."""
        if k not in capacity:
            capacity.clear()
            positions = portfolio.positions_value(marks())
            equity = portfolio.cash + positions
            headroom = Decimal(str(constraints.max_total_exposure_pct)) * equity - positions
            capacity[k] = (portfolio.cash, headroom)
        cash, headroom = capacity[k]
        price = prepared[tok].frame.price_at(k)
        if math.isnan(price):
            return None
        needed = min_order_size * fills.to_decimal(price)
        if cash < needed:
            return "insufficient_cash"
        if headroom < needed:
            return "exposure_full"
        return None

    def universe_checks(tok: str, k: int) -> list[dict[str, Any]]:
        """The universe filters at bar k, in funnel order, with actual values (PRD §13.1). Computed
        per bar from the same data as the vectorized gates."""
        frame = prepared[tok].frame
        terms = frame.terms
        close = grid.close(k)
        checks: list[dict[str, Any]] = []
        if terms.open_ts is not None:
            checks.append(
                {
                    "filter": "market_open",
                    "stage": "before_market_open",
                    "op": "gte",
                    "threshold": _iso(terms.open_ts),
                    "actual": _iso(close),
                    "passed": close >= terms.open_ts,
                }
            )
        if universe.min_volume_asof is not None:
            prefix = notional_prefix.pop(tok, None)
            if prefix is None:
                prefix = np.cumsum(frame.pr_notional)
            notional_prefix[tok] = prefix  # most recently used last
            while len(notional_prefix) > 32:
                del notional_prefix[next(iter(notional_prefix))]
            volume = frame.cum_notional_at(k - frame.k0, prefix)
            checks.append(
                {
                    "filter": "min_volume_asof",
                    "stage": "below_min_volume_asof",
                    "op": "gte",
                    "threshold": universe.min_volume_asof,
                    "actual": volume,
                    "passed": volume >= universe.min_volume_asof,
                }
            )
        if universe.min_time_to_resolution_minutes is not None:
            ttr = None if terms.end_ts is None else (terms.end_ts - close) / 60
            checks.append(
                {
                    "filter": "min_time_to_resolution_minutes",
                    "stage": "below_min_time_to_resolution",
                    "op": "gte",
                    "threshold": universe.min_time_to_resolution_minutes,
                    "actual": ttr,
                    "passed": ttr is not None and ttr >= universe.min_time_to_resolution_minutes,
                }
            )
        stale = frame.staleness_minutes_at(k)
        checks.append(
            {
                "filter": "max_stale_minutes",
                "stage": "stale_price",
                "op": "lte",
                "threshold": request.max_stale_minutes,
                "actual": None if math.isnan(stale) else stale,
                "passed": (not math.isnan(stale)) and stale <= request.max_stale_minutes,
            }
        )
        return checks

    def signal_trace(tok: str, k: int, clauses: list[dict[str, Any]]) -> dict[str, Any]:
        checks = universe_checks(tok, k)
        failed = [c["filter"] for c in checks if not c["passed"]]
        if failed:
            raise EngineInconsistency(
                f"entry signal for {tok} at {_iso(grid.ts(k))} passed the vectorized universe "
                f"gates but failed {failed} when re-checked"
            )
        return {
            "ts": _iso(grid.ts(k)),
            "decision": "ENTER",
            "clauses": _with_windows(clauses, grid.close(k)),
            "universe": checks,
        }

    def entry_trace(sig: Signal) -> dict[str, Any]:
        """The rule trace of an entry signal, built through the look-ahead-safe MarketView of the
        signal bar. For DSL strategies this also re-checks the vectorized mask (fails the run on
        disagreement). It runs for every *recorded* attempt: fills and each episode's final
        refusal."""
        if forced is not None:
            # imposed entries: the strategy's rules did not choose this bar, so there is nothing
            # to re-check and no trace is kept
            return {"ts": _iso(grid.ts(sig.signal_k)), "decision": "ENTER", "forced": forced.label}
        if sig.trace is None:
            decision = strat.evaluate_entry(view_for(sig.token_id, sig.signal_k, None))
            if not decision.enter:
                raise EngineInconsistency(
                    f"vectorized entry mask fired for {sig.token_id} at "
                    f"{_iso(grid.ts(sig.signal_k))} but MarketView evaluation did not: "
                    f"{decision.clauses}"
                )
            sig.trace = signal_trace(sig.token_id, sig.signal_k, decision.clauses)
        return sig.trace

    def finish_episode(tok: str) -> None:
        """Record an episode that ended without a fill as one trade row."""
        ep = episodes.pop(tok, None)
        if ep is None:
            return
        q.episodes.append((ep.start_k, ep.last_candidate_k, tok))
        if ep.last_row is None:
            return
        row = ep.last_row
        row["entry_rule_trace"] = {**entry_trace(row.pop("_signal")), **row["entry_rule_trace"]}
        row["seq"] = portfolio.next_seq()
        row["signal_ts"] = grid.ts(ep.start_k)
        row["entry_rule_trace"]["attempts"] = ep.attempts
        row["entry_rule_trace"]["attempt_refusals"] = dict(sorted(ep.reasons.items()))
        portfolio.rejected_rows.append(row)
        if row["fill_status"].startswith("UNEXECUTABLE"):
            q.counters["signals_unexecutable"] += 1
            q.unexecutable[row["entry_rule_trace"]["execution"].get("reason") or "UNKNOWN"] += 1
            if row["fill_status"] == FillStatus.UNEXECUTABLE_LIMIT_EXPIRED.value:
                q.counters["signals_limit_expired"] += 1
        else:
            q.counters["signals_rejected_min_size"] += 1
            sizing = row["entry_rule_trace"].get("sizing") or {}
            q.min_size_causes[sizing.get("below_min_cause") or "unknown"] += 1
            if sizing.get("below_min_cause") and sizing.get("round_up_blocked_by") is None:
                q.counters["min_size_rejections_round_up_would_fill"] += 1

    # ------------------------------------------------------------ the loop
    settle_keys = np.array(sorted(settle_at), dtype=np.int64)
    k = 0
    report_every = max(grid.n // 100, 1)
    next_report = 0
    while k < grid.n:
        if k >= next_report:
            report("simulate", k / grid.n)
            next_report = k + report_every
        # Skip to the next bar where anything can happen. With no positions that is the next
        # candidate bar. A lean run (no equity rows) of a strategy without exit rules can also skip
        # while holding: only candidates and settlements can change anything.
        if not pending and (not portfolio.open or (lean and not evaluate_exits)):
            j = int(np.searchsorted(ev_k, k, "left"))
            nxt = int(ev_k[j]) if j < ev_k.size else grid.n
            if basket_k.size:
                jb = int(np.searchsorted(basket_k, k, "left"))
                if jb < basket_k.size:
                    nxt = min(nxt, int(basket_k[jb]))
            if portfolio.open:
                js = int(np.searchsorted(settle_keys, k, "left"))
                if js < settle_keys.size:
                    nxt = min(nxt, int(settle_keys[js]))
            elif not lean:
                for kk in range(k, nxt):
                    equity_rows.append(
                        EquityRow(grid.ts(kk), portfolio.cash, ZERO, 0, portfolio.realized_pnl)
                    )
            k = nxt
            if k >= grid.n:
                break
        ts = grid.ts(k)

        # 1. mark to market: marks are read at bar k wherever they are used below (sizing,
        #    exposure, snapshot), never carried over from an earlier bar.

        # 2. settlement
        for cid in sorted(settle_at.get(k, ())):
            for pos in sorted(
                (p for p in portfolio.open.values() if p.condition_id == cid), key=lambda p: p.seq
            ):
                s = settlement.get(cid)
                payout = s.payout(pos.outcome_index)
                if payout is None:
                    continue
                frame = prepared[pos.token_id].frame
                trace: dict[str, Any] = {
                    "ts": _iso(ts),
                    "decision": "SETTLE",
                    "resolution": s.resolution,
                    "payout": str(payout),
                }
                last_obs = frame.last_observation_ts()
                assert s.resolved_ts is not None
                if last_obs is None or last_obs < s.resolved_ts - request.max_stale_minutes * 60:
                    pos.flags["settled_on_stale_data"] = True
                    trace["settled_on_stale_data"] = True
                    q.counters["settled_on_stale_data"] += 1
                fee = ZERO
                if not request.settle_winnings_fee_free:
                    fee = fee_amount(pos.shares, payout, pos.fee_terms)
                portfolio.settle(pos, ts, payout, fee, trace)
            before = len(pending)
            pending = [
                sig
                for sig in pending
                if sig.kind == "BASKET" or dataset.tokens[sig.token_id].condition_id != cid
            ]
            q.counters["signals_cancelled_by_settlement"] += before - len(pending)
            for tok in [t for t in episodes if dataset.tokens[t].condition_id == cid]:
                finish_episode(tok)
                dead_tokens.add(tok)

        # 3. exits
        pending_exit = {sig.token_id for sig in pending if sig.kind == "EXIT"}
        for pos in sorted(portfolio.open.values(), key=lambda p: p.seq) if evaluate_exits else ():
            if pos.token_id in pending_exit or pos.basket_id is not None:
                continue  # basket legs hold to settlement (v3 E3)
            wallet_sold = bool(
                watches_wallets
                and pos.signal_wallet is not None
                and prepared[pos.token_id].frame.wallet_printed(k, pos.signal_wallet, -1)
            )
            view = view_for(pos.token_id, k, pos, wallet_sold)
            assert view.position is not None
            decision = strat.evaluate_exit(view, view.position)
            if decision is not None:
                trace = {
                    "ts": _iso(ts),
                    "decision": "EXIT",
                    "reason": decision.reason.value,
                    "price": view.price,
                    "rule": decision.rule,
                    "checks": [
                        {**c, "clause": _with_windows([c["clause"]], grid.close(k))[0]}
                        if "clause" in c
                        else c
                        for c in decision.checks
                    ],
                }
                limit = _limit_for(prepared[pos.token_id].frame, k, "SELL", order)
                pending.append(
                    Signal(
                        "EXIT",
                        pos.token_id,
                        k,
                        k + lag,
                        trace,
                        decision.reason,
                        limit=limit,
                        expires_k=k + lag + (order.ttl_bars - 1 if limit is not None else 0),
                    )
                )

        # 4. entries
        pend_entries = [sig for sig in pending if sig.kind == "ENTRY"]
        pend_tokens = {sig.token_id for sig in pend_entries}
        # One position per market: with EITHER the complementary token is a different token id
        # but the same market, and holding both sides is a hedge, not a trade.
        held_conditions = {p.condition_id for p in portfolio.open.values()} | {
            dataset.tokens[t].condition_id for t in pend_tokens
        }
        pend_events = [
            dataset.markets[dataset.tokens[sig.token_id].condition_id].event_id
            for sig in pend_entries
        ]
        for tok in candidates_at(k):
            ep = episodes.get(tok)
            if ep is not None and not ep.attempt_pending and ep.last_candidate_k < k - 1:
                finish_episode(tok)  # the entry condition lapsed since the last attempt
                ep = None
            if tok in dead_tokens:
                continue
            td = dataset.tokens[tok]
            terms = dataset.markets[td.condition_id]
            blocked: str | None
            if tok in portfolio.open or tok in pend_tokens or td.condition_id in held_conditions:
                blocked = "already_holding"
            elif len(portfolio.open) + len(pend_tokens) >= constraints.max_open_positions:
                blocked = "max_open_positions"
            else:
                blocked = portfolio.can_open(
                    tok, td.condition_id, terms.event_id, ts, constraints, pend_tokens, pend_events
                )
                if not blocked and ep is None and terms.min_order_size is not None:
                    blocked = capacity_block(tok, k, terms.min_order_size)
            if blocked:
                q.blocked_by_constraint[blocked] += 1
                if ep is not None:
                    ep.last_candidate_k = k
                # A near miss when this starts a blocked stretch; holding the token itself is
                # the same signal continuing, not a missed trade.
                if (
                    near_misses_enabled
                    and blocked != "already_holding"
                    and last_blocked_k.get(tok) != k - 1
                ):
                    found = constraint_misses.setdefault(tok, [])
                    if len(found) < NM.PER_MARKET[NM.BLOCKED_BY_CONSTRAINT]:
                        found.append((k, blocked))
                last_blocked_k[tok] = k
                continue
            trace_now: dict[str, Any] | None = None
            if not isinstance(strat, DSLStrategy):
                entry_decision = strat.evaluate_entry(view_for(tok, k, None))
                if not entry_decision.enter:
                    if entry_decision.null_feature:
                        q.counters["bars_skipped_null_feature"] += 1
                    continue
                trace_now = signal_trace(tok, k, entry_decision.clauses)
            if ep is None:
                ep = episodes[tok] = _Episode(start_k=k, last_candidate_k=k)
                q.counters["signals_generated"] += 1
            ep.last_candidate_k = k
            ep.attempts += 1
            ep.attempt_pending = True
            frame_k = prepared[tok].frame
            limit = _limit_for(frame_k, k, "BUY", order)
            wallet_arr = prepared[tok].trigger_wallet
            wallet_id = None
            if wallet_arr is not None and wallet_arr[k - frame_k.k0] >= 0:
                wallet_id = int(wallet_arr[k - frame_k.k0])
            pending.append(
                Signal(
                    "ENTRY",
                    tok,
                    k,
                    k + lag,
                    trace_now,
                    limit=limit,
                    expires_k=k + lag + (order.ttl_bars - 1 if limit is not None else 0),
                    wallet=wallet_id,
                )
            )
            pend_tokens.add(tok)
            pend_events.append(terms.event_id)

        # 4b. baskets (v3 E3): one all-or-nothing order per event whose set identity is violated
        if uses_basket and basket_k.size and k in basket_k_set:
            pending_baskets = {sig.token_id for sig in pending if sig.kind == "BASKET"}
            for ev in baskets_at(k):
                if ev in pending_baskets:
                    continue
                if portfolio.open_count(ev) >= constraints.max_positions_per_event:
                    q.blocked_by_constraint["max_positions_per_event"] += 1
                    continue
                if portfolio.open_count() + len(pend_tokens) + len(pending_baskets) >= (
                    constraints.max_open_positions
                ):
                    q.blocked_by_constraint["max_open_positions"] += 1
                    continue
                assert isinstance(strat, DSLStrategy)
                ev_view = event_view(ev, k)
                fee_per_unit = 0.0
                for s_ in ev_view.siblings:
                    terms_s = dataset.markets[s_.condition_id]
                    ft = resolve_fee_terms(terms_s, ts, request.fees, dataset.fee_schedules)
                    fee_per_unit += float(
                        fee_amount(Decimal(1), fills.to_decimal(s_.price or 0.0), ft)
                    )
                basket_decision = strat.evaluate_event(ev_view, fee_per_unit)
                if basket_decision is None:
                    continue
                q.counters["baskets_signalled"] += 1
                q.counters["signals_generated"] += 1
                pending.append(
                    Signal(
                        "BASKET",
                        ev,
                        k,
                        k + lag,
                        {"ts": _iso(ts), "decision": "ENTER_BASKET", **basket_decision.trace},
                        basket=basket_decision,
                        expires_k=k + lag + (order.ttl_bars - 1 if order.type == "LIMIT" else 0),
                    )
                )
                pending_baskets.add(ev)

        # 5. execute due signals: exits first, then entries, each in signal order
        bar_marks = marks()
        due = [sig for sig in pending if sig.due_k == k]
        pending = [sig for sig in pending if sig.due_k != k]
        for sig in (
            [s_ for s_ in due if s_.kind == "EXIT"]
            + [s_ for s_ in due if s_.kind == "ENTRY"]
            + [s_ for s_ in due if s_.kind == "BASKET"]
        ):
            can_rest = sig.expires_k is not None and k < sig.expires_k
            if sig.kind == "BASKET":
                n_baskets += 1
                result_b = _execute_basket(
                    sig,
                    k,
                    request,
                    dataset,
                    prepared,
                    portfolio,
                    halted,
                    grid,
                    q,
                    bar_marks,
                    order,
                    can_rest,
                    f"B{n_baskets}",
                )
                if result_b == RESTING:
                    n_baskets -= 1
                    sig.due_k = k + 1
                    pending.append(sig)
            elif sig.kind == "EXIT":
                outcome = _execute_exit(
                    sig,
                    k,
                    request,
                    prepared,
                    portfolio,
                    halted,
                    grid,
                    q,
                    dataset.fee_schedules,
                    can_rest,
                )
                if outcome == RESTING:
                    sig.due_k = k + 1
                    pending.append(sig)
            else:
                ep = episodes[sig.token_id]
                ep.attempt_pending = False
                failure = _execute_entry(
                    sig,
                    k,
                    request,
                    dataset,
                    prepared,
                    portfolio,
                    halted,
                    grid,
                    q,
                    bar_marks,
                    ep,
                    entry_trace,
                    strat.exit_plan if isinstance(strat, DSLStrategy) else None,
                    can_rest,
                )
                if failure == RESTING:
                    ep.attempt_pending = True
                    sig.due_k = k + 1
                    pending.append(sig)
                elif failure is None:
                    done = episodes.pop(sig.token_id)
                    q.episodes.append((done.start_k, done.last_candidate_k, sig.token_id))
                else:
                    q.attempts_failed[failure] += 1
                    if failure in PERMANENT_REFUSALS:
                        finish_episode(sig.token_id)
                        dead_tokens.add(sig.token_id)

        # 6. snapshot: marks are this bar's prices for every open position (exits that closed
        #    positions leave harmless extra keys; fills above added their own marks)
        snapshot(k, bar_marks)
        k += 1

    # ------------------------------------------------------------ end of backtest
    last_k = grid.n - 1
    q.counters["signals_pending_at_end"] += sum(1 for sig in pending if sig.kind == "ENTRY")
    for tok in sorted(episodes):
        finish_episode(tok)
    end_marks = {t: fills.to_decimal(prepared[t].frame.price_at(last_k)) for t in portfolio.open}
    for pos in sorted(list(portfolio.open.values()), key=lambda p: p.seq):
        stale = prepared[pos.token_id].frame.staleness_minutes_at(last_k)
        trace = {
            "ts": _iso(grid.ts(last_k)),
            "decision": "END_OF_BACKTEST",
            "valued_at_mark": str(end_marks[pos.token_id]),
            "mark_staleness_minutes": stale,
        }
        portfolio.value_at_mark(pos, grid.ts(last_k), end_marks[pos.token_id], trace)
    if equity_rows:
        portfolio.assert_invariants({}, constraints.max_open_positions)

    if lean:
        report("simulate", 1.0)
        ending = portfolio.cash
        return LeanOutcome(
            ending_equity=ending,
            net_profit=ending - request.starting_capital,
            roi=float((ending - request.starting_capital) / request.starting_capital),
            n_trades=len(portfolio.all_positions),
            signals_generated=q.counters["signals_generated"],
            signals_filled=q.counters["signals_filled"],
        )

    q.entry_funnel = funnel

    # ------------------------------------------------------------ near misses (PRD §13.2)
    near_rows: list[dict[str, Any]] = []

    def near_miss(tok: str, k: int, kind: str, score: float | None, blocked_by: str | None) -> None:
        """Trace a non-firing bar through the look-ahead-safe MarketView, and check that it agrees
        with why the vectorized path skipped it."""
        decision = strat.evaluate_entry(view_for(tok, k, None))
        checks = universe_checks(tok, k)
        failed_filters = [c["stage"] for c in checks if not c["passed"]]
        if kind == NM.RULES_FAILED:
            consistent = not decision.enter and not failed_filters
        elif kind == NM.BLOCKED_BY_UNIVERSE:
            consistent = decision.enter and bool(failed_filters)
            blocked_by = failed_filters[0] if failed_filters else None
        else:
            consistent = decision.enter and not failed_filters
        if not consistent:
            raise EngineInconsistency(
                f"near miss {kind} for {tok} at {_iso(grid.ts(k))} disagrees with MarketView: "
                f"enter={decision.enter}, failed filters={failed_filters}"
            )
        failed_clauses = sum(1 for c in decision.clauses if not c["passed"])
        near_rows.append(
            {
                "condition_id": dataset.tokens[tok].condition_id,
                "clob_token_id": tok,
                "ts": _iso(grid.ts(k)),
                "kind": kind,
                "score": None if score is None else round(score, 6),
                "failed_clauses": failed_clauses,
                "blocked_by": blocked_by,
                "trace": {
                    "ts": _iso(grid.ts(k)),
                    "decision": "REJECT",
                    "kind": kind,
                    "blocked_by": blocked_by,
                    "score": None if score is None else round(score, 6),
                    "clauses": _with_windows(decision.clauses, grid.close(k)),
                    "universe": checks,
                },
            }
        )

    if near_misses_enabled:
        for tok in token_names:
            p = prepared[tok]
            for kind, i, score in p.near:
                near_miss(tok, p.frame.k0 + i, kind, score, None)
            for kk, reason in constraint_misses.get(tok, []):
                near_miss(tok, kk, NM.BLOCKED_BY_CONSTRAINT, None, reason)
    found_counts = Counter(str(r["kind"]) for r in near_rows)
    near_rows, capped = NM.cap_run(near_rows)
    for seq, row in enumerate(near_rows, start=1):
        row["seq"] = seq
    q.near_misses = {
        "available": near_misses_enabled,
        "note": None
        if near_misses_enabled
        else "near misses need DSL entry rules (random and custom Python entries have none)",
        "found": {kind: found_counts.get(kind, 0) for kind in NM.KINDS},
        "kept": dict(Counter(str(r["kind"]) for r in near_rows)),
        "per_market_cap": NM.PER_MARKET,
        "per_run_cap": NM.PER_RUN,
        "capped": capped,
        "separation_bars": near_miss_separation,
    }
    report("simulate", 1.0)
    result = _build_result(
        request, dataset, grid, portfolio, equity_rows, q, strat, prep.n_wanted, len(prepared)
    )
    result.near_misses = near_rows
    result.episodes = sorted(q.episodes)
    return result


def _execute_entry(
    sig: Signal,
    k: int,
    request: BacktestRequest,
    dataset: Dataset,
    prepared: dict[str, _Prepared],
    portfolio: Portfolio,
    halted: Any,
    grid: Grid,
    q: _Quality,
    marks: dict[str, Decimal],
    episode: _Episode,
    entry_trace: Any,
    exit_plan: Callable[[float], list[dict[str, Any]]] | None,
    can_rest: bool = False,
) -> str | None:
    """Attempt an entry. Returns None when a position opened, RESTING when the order waits for a
    later bar within its TTL (v3 E1), else the refusal reason (the would-be trade row is kept on
    the episode)."""
    tok = sig.token_id
    frame = prepared[tok].frame
    terms = frame.terms
    ts = grid.ts(k)
    cfg = request.execution
    side = _side_of(frame.token.outcome_index)

    def reject(
        status: FillStatus,
        reason: str | None,
        execution: dict[str, Any],
        sizing: dict[str, Any] | None,
        ref: Decimal | None,
        fill_price: Decimal | None,
    ) -> str:
        execution = {**execution, "status": status.value, "reason": reason}
        episode.reasons[reason or "UNKNOWN"] += 1
        episode.last_row = {
            "seq": None,
            "token_id": tok,
            "condition_id": terms.condition_id,
            "side": side,
            "signal_ts": grid.ts(sig.signal_k),
            "entry_ts": ts,
            # None when no price existed: a refused signal must not invent a quote.
            "entry_price": fill_price if fill_price is not None else ref,
            "entry_ref_price": ref,
            "fill_status": status.value,
            "entry_rule_trace": {"sizing": sizing, "execution": execution},
            "_signal": sig,
        }
        return reason or "UNKNOWN"

    if terms.min_order_size is None:
        return reject(
            FillStatus.UNEXECUTABLE,
            fills.MIN_SIZE_UNKNOWN,
            {"model": cfg.model.value},
            None,
            None,
            None,
        )
    quote = fills.quote(
        "BUY",
        frame,
        k,
        cfg,
        max_stale_minutes=request.max_stale_minutes,
        trading_closed=halted(tok, k),
        limit=sig.limit,
    )
    if quote.status == FillStatus.UNEXECUTABLE:
        best = quote.detail.get("best_price")
        if (
            best is not None
            and not math.isnan(best)
            and (sig.best_seen is None or best < sig.best_seen)
        ):
            sig.best_seen = float(best)
        if quote.reason in WAITABLE_REFUSALS and can_rest:
            return RESTING
        if quote.reason in WAITABLE_REFUSALS and sig.limit is not None:
            return reject(
                FillStatus.UNEXECUTABLE_LIMIT_EXPIRED,
                LIMIT_EXPIRED,
                {
                    **quote.detail,
                    "last_refusal": quote.reason,
                    "limit_price": float(sig.limit),
                    "best_price_seen": sig.best_seen,
                    "bars_waited": (k - sig.due_k) + 1,
                },
                None,
                quote.ref_price,
                None,
            )
        return reject(
            FillStatus.UNEXECUTABLE, quote.reason, quote.detail, None, quote.ref_price, None
        )
    assert quote.fill_price is not None and quote.ref_price is not None
    fee_terms = resolve_fee_terms(terms, ts, request.fees, dataset.fee_schedules)
    if fee_terms.warning:
        q.fee_warnings[fee_terms.warning] += 1
    equity = portfolio.equity(marks)
    headroom = Decimal(
        str(request.strategy.constraints.max_total_exposure_pct)
    ) * equity - portfolio.positions_value(marks)
    sized = size_entry(
        request.strategy.sizing,
        price=quote.fill_price,
        equity=equity,
        starting_capital=request.starting_capital,
        cash=portfolio.cash,
        exposure_headroom=headroom,
        liquidity_cap=quote.max_shares,
        fee=fee_terms,
        share_precision=request.share_precision,
        min_order_size=terms.min_order_size,
    )
    sizing_trace: dict[str, Any] = {
        "mode": request.strategy.sizing.mode.value,
        "requested_notional": _q6(sized.requested_notional),
        "final_notional": _q6(sized.final_notional),
        "size_binding_constraint": sized.binding,
        "equity": _q6(equity),
        "min_order_policy": request.strategy.sizing.min_order_policy,
    }
    if sized.rounded_up_to_min:
        sizing_trace["rounded_up_to_min_size"] = True
        q.counters["entries_rounded_up_to_min_size"] += 1
    if sized.below_min_cause is not None:
        sizing_trace["below_min_cause"] = sized.below_min_cause
        sizing_trace["round_up_blocked_by"] = sized.round_up_blocked_by
    fee = fee_amount(sized.shares, quote.fill_price, fee_terms)
    execution = {
        **quote.detail,
        "shares": str(sized.shares),
        "fee_rate": str(fee_terms.rate),
        "fee_exponent": fee_terms.exponent,
        "fee_schedule": fee_terms.label,
        "fee": str(fee),
    }
    if (
        sized.liquidity_limited
        and request.execution.partial_fill_policy == PartialFillPolicy.REJECT
    ):
        return reject(
            FillStatus.UNEXECUTABLE,
            fills.LIQUIDITY_CAP,
            execution,
            sizing_trace,
            quote.ref_price,
            quote.fill_price,
        )
    if sized.shares <= ZERO or sized.shares < terms.min_order_size:
        return reject(
            FillStatus.REJECTED_MIN_SIZE,
            "BELOW_MIN_ORDER_SIZE",
            {**execution, "min_order_size": str(terms.min_order_size)},
            sizing_trace,
            quote.ref_price,
            quote.fill_price,
        )
    if request.check_invariants and sized.final_notional > max(headroom, ZERO):
        raise AssertionError(
            f"entry notional {sized.final_notional} exceeds exposure headroom {headroom}"
        )
    status = FillStatus.PARTIAL if sized.liquidity_limited else FillStatus.FILLED
    if fee_terms.label:
        q.fee_labels.add(fee_terms.label)
    q.counters["signals_filled"] += 1
    execution["status"] = status.value
    portfolio.open_position(
        Position(
            seq=portfolio.next_seq(),
            token_id=tok,
            condition_id=terms.condition_id,
            event_id=terms.event_id,
            outcome_index=frame.token.outcome_index,
            side=side,
            signal_ts=grid.ts(episode.start_k),
            entry_ts=ts,
            entry_price=quote.fill_price,
            entry_ref_price=quote.ref_price,
            initial_shares=sized.shares,
            entry_fee=fee,
            fee_terms=fee_terms,
            entry_trace={
                **entry_trace(sig),
                "sizing": sizing_trace,
                "execution": execution,
                "attempts": episode.attempts,
                "attempt_refusals": dict(sorted(episode.reasons.items())),
                "exit_plan": [
                    {**item, "deadline": _iso(ts + item["limit_minutes"] * 60)}
                    if "limit_minutes" in item
                    else item
                    for item in (exit_plan(float(quote.fill_price)) if exit_plan else [])
                ],
            },
            fill_status=status,
            signal_wallet=sig.wallet,
        )
    )
    marks[tok] = fills.to_decimal(frame.price_at(k))
    return None


def _execute_exit(
    sig: Signal,
    k: int,
    request: BacktestRequest,
    prepared: dict[str, _Prepared],
    portfolio: Portfolio,
    halted: Any,
    grid: Grid,
    q: _Quality,
    fee_schedules: tuple[FeeScheduleEntry, ...],
    can_rest: bool = False,
) -> str | None:
    """Returns RESTING when the sell order waits within its TTL, else None."""
    pos = portfolio.open.get(sig.token_id)
    if pos is None:
        return None
    frame = prepared[sig.token_id].frame
    ts = grid.ts(k)
    quote = fills.quote(
        "SELL",
        frame,
        k,
        request.execution,
        max_stale_minutes=request.max_stale_minutes,
        trading_closed=halted(sig.token_id, k),
        limit=sig.limit,
    )
    if quote.status == FillStatus.UNEXECUTABLE:
        if quote.reason in WAITABLE_REFUSALS and can_rest:
            return RESTING
        reason = LIMIT_EXPIRED if quote.reason in WAITABLE_REFUSALS and sig.limit else quote.reason
        q.exit_unexecutable[reason or "UNKNOWN"] += 1
        return None  # position stays open; the exit rule is re-evaluated next bar
    assert quote.fill_price is not None and quote.ref_price is not None
    shares = pos.shares
    step = Decimal(1).scaleb(-request.share_precision)
    if quote.max_shares is not None:
        cap = quote.max_shares.quantize(step, rounding=ROUND_DOWN)
        if cap < shares:
            if request.execution.partial_fill_policy == PartialFillPolicy.REJECT:
                q.exit_unexecutable[fills.LIQUIDITY_CAP] += 1
                return None
            shares = cap
    if shares <= ZERO:
        q.exit_unexecutable[fills.LIQUIDITY_CAP] += 1
        return None
    fee_terms = resolve_fee_terms(frame.terms, ts, request.fees, fee_schedules)
    if fee_terms.warning:
        q.fee_warnings[fee_terms.warning] += 1
    fee = fee_amount(shares, quote.fill_price, fee_terms)
    assert sig.reason is not None
    fill = {
        **quote.detail,
        "ts": _iso(ts),
        # which exit decision this fill executed: a position can take several partial fills, each
        # from its own (re-evaluated) exit signal
        "signal_ts": (sig.trace or {}).get("ts"),
        "signal_reason": sig.reason.value,
        "shares": str(shares),
        "fee": str(fee),
        "fee_rate": str(fee_terms.rate),
        "fee_schedule": fee_terms.label,
    }
    if fee_terms.label:
        q.fee_labels.add(fee_terms.label)
    portfolio.reduce(pos, ts, shares, quote.fill_price, quote.ref_price, fee, fill)
    if pos.shares == ZERO:
        portfolio.close(pos, ts, sig.reason, {**(sig.trace or {}), "fills": pos.exit_fills})
    else:
        q.counters["partial_exits"] += 1
    return None


def _basket_execution(legs: list[dict[str, Any]]) -> dict[str, Any]:
    vols = []
    for leg in legs:
        ex = leg["entry_rule_trace"].get("execution") or {}
        vol = ex.get("eligible_print_volume", ex.get("bar_print_volume"))
        if vol is not None:
            vols.append(float(vol))
    cost = sum((Decimal(leg["entry_price"]) for leg in legs), ZERO)
    return {
        "model": "BASKET",
        "eligible_print_volume": min(vols) if len(vols) == len(legs) else None,
        "vwap": float(cost),
        "status": "FILLED",
    }


def _capacity(trades: list[dict[str, Any]], request: BacktestRequest) -> dict[str, Any]:
    """v3 E5 capacity: per signal, min(requested notional, participation × same-side print
    notional in the fill bar), summed per month of the signal span (first signal to last
    attempt; at least one day), at $1,000 and $10,000 starting capital. Sizing modes that scale
    with capital scale the request; fixed-cash sizing does not."""
    sizing = request.strategy.sizing
    part = request.execution.liquidity_participation
    scales = sizing.mode in (SizingMode.PCT_OF_EQUITY, SizingMode.PCT_OF_INITIAL)
    cap_per_trade = sizing.max_notional_per_trade
    rows = [t for t in trades if t.get("basket_role") != "LEG"]
    stamps = [_from_iso(t["signal_ts"]) for t in rows] + [_from_iso(t["entry_ts"]) for t in rows]
    span_days = (max(stamps) - min(stamps)) / 86_400 if stamps else 0.0
    months = max(span_days, 1.0) / 30.4375
    out: dict[str, Any] = {
        "signals": len(rows),
        "months": months,
        "participation": part,
        "method": "Σ over signals of min(requested notional, participation × same-side print "
        "notional in the fill bar), per month of the signal span; requests under PCT sizing "
        "scale with capital",
    }
    for capital in (1000, 10000):
        captured = 0.0
        constrained = 0
        for t in rows:
            entry = t["entry_rule_trace"] or {}
            sized = entry.get("sizing") or {}
            ex = entry.get("execution") or {}
            if sized.get("requested_notional") is not None:
                req = float(sized["requested_notional"])
                if scales:
                    req *= capital / float(request.starting_capital)
            else:  # refused before sizing: what the rule would have asked for
                value = float(sizing.value)
                price = float(t["entry_ref_price"] or 0.5)
                req = {
                    SizingMode.FIXED_CASH: value,
                    SizingMode.PCT_OF_EQUITY: value * capital,
                    SizingMode.PCT_OF_INITIAL: value * capital,
                    SizingMode.FIXED_SHARES: value * price,
                }[sizing.mode]
            if cap_per_trade is not None:
                req = min(req, float(cap_per_trade))
            if part is None:
                avail = math.inf
            else:
                vol = ex.get("eligible_print_volume", ex.get("bar_print_volume"))
                px = ex.get("vwap", ex.get("ref_price"))
                avail = 0.0 if vol is None or px is None else part * float(vol) * float(px)
            captured += min(req, avail)
            constrained += avail < req
        out[str(capital)] = {
            "notional_per_month": captured / months,
            "share_size_constrained": constrained / len(rows) if rows else None,
        }
    return out


def _basket_summaries(trades: list[dict[str, Any]], portfolio: Portfolio) -> list[dict[str, Any]]:
    """One SUMMARY row per filled basket: Σ over its legs (v3 E3)."""
    legs_by: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        if t.get("basket_role") == "LEG":
            legs_by.setdefault(str(t["basket_id"]), []).append(t)
    out = []
    for bid, legs in sorted(legs_by.items(), key=lambda kv: kv[1][0]["seq"]):

        def d(key: str, legs: list[dict[str, Any]] = legs) -> Decimal:
            return sum((Decimal(leg[key]) for leg in legs), ZERO)

        settled = all(leg["exit_reason"] == "MARKET_RESOLVED" for leg in legs)
        closed = all(leg["exit_ts"] is not None for leg in legs)
        cost = d("notional") + d("entry_fee")
        net = d("net_pnl")
        exit_ts = max((leg["exit_ts"] for leg in legs if leg["exit_ts"]), default=None)
        out.append(
            {
                "seq": portfolio.next_seq(),
                "condition_id": f"event:{legs[0]['entry_rule_trace'].get('event_id', '')}",
                "clob_token_id": bid,
                "side": "BASKET",
                "signal_ts": legs[0]["signal_ts"],
                "entry_ts": legs[0]["entry_ts"],
                "entry_price": _q6(d("entry_price")),  # Σ leg fills = cost per unit
                "entry_ref_price": _q6(d("entry_ref_price")),
                "shares": legs[0]["shares"],  # units
                "notional": _q6(d("notional")),
                "entry_fee": _q6(d("entry_fee")),
                "exit_ts": exit_ts,
                "exit_price": _q6(sum((Decimal(leg["exit_price"] or 0) for leg in legs), ZERO))
                if closed
                else None,
                "exit_fee": _q6(d("exit_fee")) if closed else None,
                "exit_reason": "MARKET_RESOLVED"
                if settled
                else (legs[0]["exit_reason"] if closed else None),
                "gross_pnl": _q6(d("gross_pnl")),
                "fees_total": _q6(d("fees_total")),
                "slippage_cost": _q6(d("slippage_cost")),
                "net_pnl": _q6(net),
                "return_pct": _q6(net / cost) if cost else "0.000000",
                "holding_seconds": max((leg["holding_seconds"] or 0) for leg in legs)
                if closed
                else None,
                "entry_rule_trace": {
                    "ts": legs[0]["entry_rule_trace"].get("ts"),
                    "decision": "BASKET_SUMMARY",
                    "basket": legs[0]["entry_rule_trace"].get("basket"),
                    "legs": [leg["seq"] for leg in legs],
                    "sizing": legs[0]["entry_rule_trace"].get("sizing"),
                    # capacity (v3 E5): a basket's available units are the scarcest leg's
                    "execution": _basket_execution(legs),
                    "attempts": 1,
                },
                "exit_rule_trace": {
                    "decision": "BASKET_SETTLED" if settled else "BASKET_OPEN",
                    "legs": [leg["seq"] for leg in legs],
                },
                "fill_status": "PARTIAL"
                if any(leg["fill_status"] == "PARTIAL" for leg in legs)
                else "FILLED",
                "basket_id": bid,
                "basket_role": "SUMMARY",
            }
        )
    return out


def _execute_basket(
    sig: Signal,
    k: int,
    request: BacktestRequest,
    dataset: Dataset,
    prepared: dict[str, _Prepared],
    portfolio: Portfolio,
    halted: Any,
    grid: Grid,
    q: _Quality,
    marks: dict[str, Decimal],
    order: Any,
    can_rest: bool,
    basket_id: str,
) -> str | None:
    """All-or-nothing purchase of one unit of each leg × units (v3 E3). Returns None when the
    basket opened, RESTING when a leg's limit order waits, else the refusal."""
    decision: BasketDecision = sig.basket
    ts = grid.ts(k)
    cfg = request.execution
    side_value = "BUY_YES" if decision.kind == "NEGRISK_YES_SET" else "BUY_NO"
    quotes = []
    for tok in decision.legs:
        frame = prepared[tok].frame
        limit = _limit_for(frame, sig.signal_k, "BUY", order)
        quotes.append(
            (
                tok,
                fills.quote(
                    "BUY",
                    frame,
                    k,
                    cfg,
                    max_stale_minutes=request.max_stale_minutes,
                    trading_closed=halted(tok, k),
                    limit=limit,
                ),
            )
        )
    refused = [(tok, qt) for tok, qt in quotes if qt.status == FillStatus.UNEXECUTABLE]

    def reject(status: FillStatus, reason: str, detail: dict[str, Any]) -> str:
        q.counters["baskets_unexecutable"] += 1
        if status == FillStatus.REJECTED_MIN_SIZE:
            q.counters["signals_rejected_min_size"] += 1
        else:
            q.counters["signals_unexecutable"] += 1
            q.unexecutable[reason] += 1
        portfolio.rejected_rows.append(
            {
                "seq": portfolio.next_seq(),
                "token_id": basket_id,
                "condition_id": f"event:{sig.token_id}",
                "side": "BASKET",
                "signal_ts": grid.ts(sig.signal_k),
                "entry_ts": ts,
                "entry_price": None,
                "entry_ref_price": None,
                "fill_status": status.value,
                "entry_rule_trace": {
                    **(sig.trace or {}),
                    "event_id": sig.token_id,
                    "legs": list(decision.legs),
                    "sizing": None,
                    "execution": {"status": status.value, "reason": reason, **detail},
                    "attempts": 1,
                    "attempt_refusals": {reason: 1},
                },
                "basket_id": basket_id,
                "basket_role": "SUMMARY",
            }
        )
        return reason

    if refused:
        if can_rest and all(qt.reason in WAITABLE_REFUSALS for _, qt in refused):
            return RESTING
        tok, qt = refused[0]
        waited_out = order.type == "LIMIT" and qt.reason in WAITABLE_REFUSALS
        return reject(
            FillStatus.UNEXECUTABLE_BASKET_LEG,
            "BASKET_LEG_" + (LIMIT_EXPIRED if waited_out else str(qt.reason)),
            {"leg": tok, "leg_refusal": qt.reason, **qt.detail},
        )

    legs = []
    cost_per_unit = ZERO
    fee_per_unit = ZERO
    for tok, qt in quotes:
        assert qt.fill_price is not None and qt.ref_price is not None
        terms = prepared[tok].frame.terms
        if terms.min_order_size is None:
            return reject(
                FillStatus.UNEXECUTABLE_BASKET_LEG, "BASKET_LEG_MIN_SIZE_UNKNOWN", {"leg": tok}
            )
        ft = resolve_fee_terms(terms, ts, request.fees, dataset.fee_schedules)
        legs.append((tok, qt, terms, ft))
        cost_per_unit += qt.fill_price
        fee_per_unit += fee_amount(Decimal(1), qt.fill_price, ft)

    sizing = request.strategy.sizing
    step = Decimal(1).scaleb(-request.share_precision)
    equity = portfolio.equity(marks)
    value = Decimal(str(sizing.value))
    requested = {
        "FIXED_CASH": value,
        "PCT_OF_EQUITY": value * equity,
        "PCT_OF_INITIAL": value * request.starting_capital,
        "FIXED_SHARES": value * cost_per_unit,
    }[sizing.mode.value]
    units = (requested / cost_per_unit).quantize(step, rounding=ROUND_DOWN)
    binding: str | None = None
    if sizing.max_notional_per_trade is not None:
        cap = (Decimal(str(sizing.max_notional_per_trade)) / cost_per_unit).quantize(
            step, rounding=ROUND_DOWN
        )
        if cap < units:
            units, binding = cap, "max_notional_per_trade"
    cash_cap = (portfolio.cash / (cost_per_unit + fee_per_unit)).quantize(step, rounding=ROUND_DOWN)
    if cash_cap < units:
        units, binding = cash_cap, "cash"
    headroom = Decimal(str(request.strategy.constraints.max_total_exposure_pct)) * equity - (
        portfolio.positions_value(marks)
    )
    exposure_cap = (max(headroom, ZERO) / cost_per_unit).quantize(step, rounding=ROUND_DOWN)
    if exposure_cap < units:
        units, binding = exposure_cap, "max_total_exposure_pct"
    liquidity_limited = False
    for _tok, qt, _terms, _ft in legs:
        if qt.max_shares is not None:
            cap = qt.max_shares.quantize(step, rounding=ROUND_DOWN)
            if cap < units:
                units, binding, liquidity_limited = cap, "liquidity", True
    if liquidity_limited and cfg.partial_fill_policy == PartialFillPolicy.REJECT:
        return reject(FillStatus.UNEXECUTABLE_BASKET_LEG, "BASKET_LEG_LIQUIDITY_CAP", {})
    min_units = max(t.min_order_size for _, _, t, _ in legs if t.min_order_size is not None)
    if units < min_units:
        target = min_units.quantize(step, rounding=ROUND_UP)
        affordable = (
            sizing.min_order_policy == "ROUND_UP"
            and target * cost_per_unit <= requested * Decimal(str(sizing.round_up_max_multiple))
            and target * (cost_per_unit + fee_per_unit) <= portfolio.cash
            and target * cost_per_unit <= max(headroom, ZERO)
            and all(qt.max_shares is None or target <= qt.max_shares for _, qt, _, _ in legs)
        )
        if not affordable:
            q.counters["signals_rejected_min_size"] += 1
            return reject(
                FillStatus.REJECTED_MIN_SIZE,
                "BELOW_MIN_ORDER_SIZE",
                {"units": str(units), "min_units": str(min_units)},
            )
        units, binding = target, "min_order_size"
    if units <= ZERO:
        return reject(FillStatus.REJECTED_MIN_SIZE, "BELOW_MIN_ORDER_SIZE", {"units": str(units)})

    sizing_trace = {
        "mode": sizing.mode.value,
        "requested_notional": _q6(requested),
        "final_notional": _q6(units * cost_per_unit),
        "size_binding_constraint": binding,
        "equity": _q6(equity),
        "units": str(units),
        "cost_per_unit": _q6(cost_per_unit),
        "fee_per_unit": _q6(fee_per_unit),
    }
    status = FillStatus.PARTIAL if liquidity_limited else FillStatus.FILLED
    for tok, qt, terms, ft in legs:
        assert qt.fill_price is not None and qt.ref_price is not None
        fee = fee_amount(units, qt.fill_price, ft)
        if ft.label:
            q.fee_labels.add(ft.label)
        portfolio.open_position(
            Position(
                seq=portfolio.next_seq(),
                token_id=tok,
                condition_id=terms.condition_id,
                event_id=terms.event_id,
                outcome_index=prepared[tok].frame.token.outcome_index,
                side=side_value,
                signal_ts=grid.ts(sig.signal_k),
                entry_ts=ts,
                entry_price=qt.fill_price,
                entry_ref_price=qt.ref_price,
                initial_shares=units,
                entry_fee=fee,
                fee_terms=ft,
                entry_trace={
                    **(sig.trace or {}),
                    "event_id": sig.token_id,
                    "basket": {**decision.trace, "legs": list(decision.legs)},
                    "sizing": sizing_trace,
                    "execution": {
                        **qt.detail,
                        "shares": str(units),
                        "fee": str(fee),
                        "status": status.value,
                    },
                    "attempts": 1,
                    "attempt_refusals": {},
                    "exit_plan": [],
                    "universe": [],
                    "clauses": [],
                },
                fill_status=status,
                basket_id=basket_id,
            )
        )
        marks[tok] = fills.to_decimal(prepared[tok].frame.price_at(k))
    q.counters["baskets_filled"] += 1
    q.counters["signals_filled"] += 1
    return None


def _build_result(
    request: BacktestRequest,
    dataset: Dataset,
    grid: Grid,
    portfolio: Portfolio,
    equity_rows: list[EquityRow],
    q: _Quality,
    strat: Strategy,
    n_selected: int,
    n_usable: int,
) -> BacktestResult:
    trades: list[dict[str, Any]] = []
    for pos in portfolio.all_positions:
        shares = pos.initial_shares
        gross = pos.exit_ref_value - pos.entry_ref_price * shares
        slippage = (pos.entry_price - pos.entry_ref_price) * shares + (
            pos.exit_ref_value - pos.exit_value
        )
        fees = pos.entry_fee + pos.exit_fees
        net = pos.exit_value - pos.entry_price * shares - fees
        exit_price = pos.exit_value / shares if shares else ZERO
        trades.append(
            {
                "seq": pos.seq,
                "condition_id": pos.condition_id,
                "clob_token_id": pos.token_id,
                "side": pos.side,
                "signal_ts": _iso(pos.signal_ts),
                "entry_ts": _iso(pos.entry_ts),
                "entry_price": _q6(pos.entry_price),
                "entry_ref_price": _q6(pos.entry_ref_price),
                "shares": _q6(shares),
                "notional": _q6(shares * pos.entry_price),
                "entry_fee": _q6(pos.entry_fee),
                "exit_ts": _iso(pos.exit_ts),
                "exit_price": _q6(exit_price),
                "exit_fee": _q6(pos.exit_fees),
                "exit_reason": pos.exit_reason.value if pos.exit_reason else None,
                "gross_pnl": _q6(gross),
                "fees_total": _q6(fees),
                "slippage_cost": _q6(slippage),
                "net_pnl": _q6(net),
                "return_pct": _q6(net / pos.entry_cost) if pos.entry_cost else "0.000000",
                "holding_seconds": (pos.exit_ts - pos.entry_ts)
                if pos.exit_ts is not None
                else None,
                "entry_rule_trace": pos.entry_trace,
                "exit_rule_trace": {
                    **(pos.exit_trace or {}),
                    **({"flags": pos.flags} if pos.flags else {}),
                },
                "fill_status": pos.fill_status.value,
                "basket_id": pos.basket_id,
                "basket_role": "LEG" if pos.basket_id is not None else None,
            }
        )
    for row in portfolio.rejected_rows:
        z = "0.000000"
        trades.append(
            {
                "seq": row["seq"],
                "condition_id": row["condition_id"],
                "clob_token_id": row["token_id"],
                "side": row["side"],
                "signal_ts": _iso(row["signal_ts"]),
                "entry_ts": _iso(row["entry_ts"]),
                "entry_price": _q6_or_none(row["entry_price"]),
                "entry_ref_price": _q6_or_none(row["entry_ref_price"]),
                "shares": z,
                "notional": z,
                "entry_fee": z,
                "exit_ts": None,
                "exit_price": None,
                "exit_fee": None,
                "exit_reason": None,
                "gross_pnl": z,
                "fees_total": z,
                "slippage_cost": z,
                "net_pnl": z,
                "return_pct": z,
                "holding_seconds": None,
                "entry_rule_trace": row["entry_rule_trace"],
                "exit_rule_trace": None,
                "fill_status": row["fill_status"],
                "basket_id": row.get("basket_id"),
                "basket_role": row.get("basket_role"),
            }
        )
    trades.extend(_basket_summaries(trades, portfolio))
    trades.sort(key=lambda t: t["seq"])

    equity: list[dict[str, Any]] = []
    points: list[EquityPoint] = []
    for r in equity_rows:
        eq = r.cash + r.positions_value
        unrealized = eq - request.starting_capital - r.realized_pnl
        equity.append(
            {
                "ts": _iso(r.ts),
                "cash": _q6(r.cash),
                "positions_value": _q6(r.positions_value),
                "equity": _q6(eq),
                "open_positions": r.open_positions,
                "realized_pnl": _q6(r.realized_pnl),
                "unrealized_pnl": _q6(unrealized),
                "exposure_pct": str((r.positions_value / eq).quantize(Decimal("0.0001")))
                if eq > 0
                else "0.0000",
            }
        )
        points.append(EquityPoint(r.ts, eq))
    if request.check_invariants and points and portfolio.cash != points[-1].equity:
        raise AssertionError(
            f"end-of-backtest valuation changed equity: {points[-1].equity} -> {portfolio.cash}"
        )

    ledger = [
        {
            "ts": _iso(e.ts),
            "type": e.type,
            "amount": _q6(e.amount),
            "running_cash": _q6(e.running_cash),
            "ref": e.ref,
        }
        for e in portfolio.ledger
    ]

    bias_warnings: list[str] = []
    if request.strategy.order.type == "MARKET":
        bias_warnings.append("MARKET ORDER — NO PRICE PROTECTION")
    if (
        request.execution.model.value == "TRADE_PRINT"
        and request.execution.liquidity_side.value == "BOTH"
    ):
        bias_warnings.append("BOTH-SIDE LIQUIDITY — OPTIMISTIC")
    selection = (dataset.selection or {}).get("selection_uses_final_volume")
    if selection:
        safe = float(selection["survivorship_free_for_min_volume_asof_at_least"])
        asof = request.strategy.universe.min_volume_asof
        if asof is None or asof < safe:
            bias_warnings.append(
                f"SURVIVORSHIP BIAS — this collection was pre-filtered on final lifetime volume "
                f"(>= {selection['min_lifetime_volume']:g}); results are only unbiased for strategies "
                f"with min_volume_asof >= {safe:g}, but this run uses {asof}."
            )
    metrics = compute_metrics(
        starting_capital=request.starting_capital,
        equity_points=points,
        trades=trades,
        decision_lag_bars=request.decision_lag_bars,
        execution_model=request.execution.model.value,
        fee_mode=request.fees.mode.value,
        extra_warnings=bias_warnings,
        capacity=_capacity(trades, request),
    )
    c = q.counters
    bars_total = c["bars_total"]
    generated = c["signals_generated"]
    unexecuted = c["signals_unexecutable"] + c["signals_rejected_min_size"]
    unexecuted_share = unexecuted / generated if generated else 0.0
    if generated == 0:
        execution_state = "NO_SIGNALS"
    elif unexecuted_share >= PARTIAL_EXECUTION_SHARE:
        execution_state = "PARTIAL"
    else:
        execution_state = "COMPLETE"
    data_quality = {
        # PRD §16: generated vs filled is the most informative single ratio in the product.
        "signal_fill_rate": c["signals_filled"] / generated if generated else None,
        "unexecuted_signal_share": unexecuted_share,
        "execution_state": execution_state,
        "dataset_version": dataset.dataset_version_id,
        "coverage": {"start": _iso(grid.start), "end": _iso(grid.ts(grid.n - 1))},
        "bar_interval_minutes": request.bar_interval_minutes,
        "markets_selected": n_selected,
        "markets_with_usable_data": n_usable,
        "markets_dropped": q.markets_dropped,
        "bars_total": bars_total,
        "bars_blocked_stale": c["bars_blocked_stale"],
        "entry_funnel": {
            key: q.entry_funnel[key]
            for key in (
                "no_price_yet",
                "trading_halted",
                "before_market_open",
                "below_min_volume_asof",
                "below_min_time_to_resolution",
                "stale_price",
                "eligible",
                "entry_rules_true_when_eligible",
                "entry_rules_true_ignoring_universe",
            )
            if key in q.entry_funnel or key == "eligible"
        },
        "stale_pct": round(100 * c["bars_blocked_stale"] / bars_total, 2) if bars_total else 0.0,
        "prints_total": c["prints_total"],
        "bars_with_no_prints_pct": round(100 * c["bars_with_no_prints"] / bars_total, 2)
        if bars_total
        else 0.0,
        "orderbook_coverage_pct": 0.0,
        "signals_generated": c["signals_generated"],
        "signals_filled": c["signals_filled"],
        "signals_unexecutable": c["signals_unexecutable"],
        "signals_unexecutable_reasons": dict(sorted(q.unexecutable.items())),
        "signals_rejected_min_size": c["signals_rejected_min_size"],
        # why orders fell below the market minimum, and how many ROUND_UP would have filled
        "signals_rejected_min_size_by_cause": dict(sorted(q.min_size_causes.items())),
        "min_size_rejections_round_up_would_fill": c["min_size_rejections_round_up_would_fill"],
        "entries_rounded_up_to_min_size": c["entries_rounded_up_to_min_size"],
        "signals_pending_at_end": c["signals_pending_at_end"],
        "signals_cancelled_by_settlement": c["signals_cancelled_by_settlement"],
        # v3 E1/E3
        "signals_limit_expired": c["signals_limit_expired"],
        "baskets_signalled": c["baskets_signalled"],
        "baskets_filled": c["baskets_filled"],
        "baskets_unexecutable": c["baskets_unexecutable"],
        "order_type": request.strategy.order.type,
        "liquidity_side": request.execution.liquidity_side.value,
        "signals_blocked_by_constraint": dict(sorted(q.blocked_by_constraint.items())),
        "exit_attempts_unexecutable": dict(sorted(q.exit_unexecutable.items())),
        "partial_exits": c["partial_exits"],
        "settled_on_stale_data": c["settled_on_stale_data"],
        "bars_skipped_null_feature": c["bars_skipped_null_feature"],
        "fee_mode": request.fees.mode.value,
        "fee_schedules_applied": sorted(q.fee_labels),
        "fee_warnings": dict(sorted(q.fee_warnings.items())),
        "near_misses": q.near_misses,
        "selection": dataset.selection,
        "bias_warnings": bias_warnings,
        "execution_model": request.execution.model.value,
        "decision_lag_bars": request.decision_lag_bars,
        "assumptions": [
            "bar prices are sampled mid-like prices, not trades (docs/DATA_NOTES.md)",
            "no fee at settlement"
            if request.settle_winnings_fee_free
            else "settlement charged as a taker fill",
            "exits sell the whole remaining position without a minimum-size check",
            "open positions at the end are valued at their last mark, without costs",
        ],
    }
    manifest = {
        "engine_version": ENGINE_VERSION,
        "config_hash": request.config_hash(),
        "dsl_hash": request.strategy.dsl_hash(),
        "dataset_version_id": dataset.dataset_version_id,
        "seed": request.seed,
        "strategy": strat.name,
    }
    return BacktestResult(manifest, trades, equity, ledger, metrics, data_quality)
