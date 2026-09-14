"""Replay payloads (PRD §13.3): the price and prints around one trade or near miss, and the
geometry of the decision (markers, lookback windows, exit levels) taken from its stored traces.

Nothing here is a metric. Geometry is read from traces the engine wrote; prices and prints come
straight from `price_bars` and `trade_prints`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import psycopg

from syntha_api.lttb import lttb_indices
from syntha_contracts.api import (
    NearMissDetail,
    ReplayLevel,
    ReplayMarker,
    ReplayMarket,
    ReplayPrints,
    ReplaySeries,
    ReplayWindow,
    TradeDetail,
)

PADDING = timedelta(hours=2)  # PRD §13.3: ±2 hours around the trade
MAX_PRICE_POINTS = 4000
MAX_PRINTS = 20_000

LEVEL_LABELS = {
    "TAKE_PROFIT_PCT": "Take profit",
    "STOP_LOSS_PCT": "Stop loss",
    "PRICE_ABOVE": "Exit above",
    "PRICE_BELOW": "Exit below",
}


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _num(value: Any) -> str:
    """A stored decimal string without trailing zeros: '11.960000' -> '11.96'."""
    if value is None:
        return "—"
    text = str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


OPS = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥", "eq": "=", "neq": "≠"}

BLOCKERS = {
    "before_market_open": "before market open",
    "below_min_volume_asof": "below min volume as of the bar",
    "below_min_time_to_resolution": "too close to scheduled end",
    "stale_price": "stale price",
    "max_open_positions": "max open positions reached",
    "max_positions_per_event": "already holding this event",
    "reentry_not_allowed": "re-entry not allowed",
    "cooldown": "market cooldown",
}


def why_not(blocked_by: str | None, failed_clauses: int) -> str:
    if blocked_by:
        return BLOCKERS.get(blocked_by, blocked_by)
    return f"{failed_clauses} rule{'s' if failed_clauses != 1 else ''} failed"


def feature_label(clause: dict[str, Any]) -> str:
    params = clause.get("params") or {}
    inner = ", ".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{clause['feature']}({inner})" if inner else str(clause["feature"])


class Geometry:
    def __init__(self) -> None:
        self.anchors: list[datetime] = []
        self.markers: list[ReplayMarker] = []
        self.windows: list[ReplayWindow] = []
        self.levels: list[ReplayLevel] = []

    def mark(
        self,
        ts: datetime | None,
        kind: str,
        label: str,
        price: float | None = None,
        ref: int | None = None,
    ) -> None:
        if ts is None:
            return
        self.anchors.append(ts)
        self.markers.append(ReplayMarker(ts=ts, kind=kind, price=price, label=label, ref=ref))

    def clause_windows(self, clauses: list[dict[str, Any]], side: str) -> None:
        for clause in clauses:
            window = clause.get("window")
            if not window:
                continue
            start, end = _dt(window["from"]), _dt(window["to"])
            assert start is not None and end is not None
            self.anchors.append(start)
            self.windows.append(
                ReplayWindow(
                    label=feature_label(clause),
                    start=start,
                    end=end,
                    passed=clause.get("passed"),
                    side=side,
                )
            )

    def price_rules(self, clauses: list[dict[str, Any]]) -> None:
        for clause in clauses:
            if clause.get("feature") == "price" and isinstance(
                clause.get("threshold"), int | float
            ):
                self.levels.append(
                    ReplayLevel(
                        kind="ENTRY_PRICE_RULE",
                        price=float(clause["threshold"]),
                        label=f"Entry rule: price {OPS.get(clause['op'], clause['op'])} {clause['threshold']}",
                    )
                )

    def span(self) -> tuple[datetime, datetime]:
        return min(self.anchors) - PADDING, max(self.anchors) + PADDING


def trade_geometry(trade: TradeDetail) -> Geometry:
    g = Geometry()
    entry = trade.entry_rule_trace or {}
    attempts = entry.get("attempts")
    g.mark(
        trade.signal_ts,
        "SIGNAL",
        "Signal" + (f" ({attempts} attempt{'s' if attempts != 1 else ''})" if attempts else ""),
    )
    decision_ts = _dt(entry.get("ts"))
    if decision_ts is not None and decision_ts != trade.signal_ts:
        g.mark(decision_ts, "SIGNAL", "Decision bar of the recorded attempt")
    if trade.fill_status in ("FILLED", "PARTIAL"):
        g.mark(
            trade.entry_ts,
            "ENTRY",
            f"Entry {_num(trade.entry_price)} × {_num(trade.shares)}",
            _float(trade.entry_price),
        )
    else:
        g.mark(
            trade.entry_ts,
            "ATTEMPT",
            f"{trade.fill_status}: {trade.refusal_reason or 'refused'}",
            _float(trade.entry_price),
        )
    g.clause_windows(entry.get("clauses") or [], "entry")
    g.price_rules(entry.get("clauses") or [])
    for item in entry.get("exit_plan") or []:
        if item.get("level") is not None:
            pct = (
                f" {'+' if item['type'] == 'TAKE_PROFIT_PCT' else '−'}{item['value']:.0%}"
                if "value" in item
                else ""
            )
            g.levels.append(
                ReplayLevel(
                    kind=item["type"],
                    price=float(item["level"]),
                    label=f"{LEVEL_LABELS.get(item['type'], item['type'])}{pct} @ {item['level']:.4f}",
                )
            )

    exit_trace = trade.exit_rule_trace or {}
    decision = exit_trace.get("decision")
    exit_ts = _dt(exit_trace.get("ts"))
    if decision == "EXIT":
        # One marker per exit decision that produced a fill (partial exits re-signal), falling back
        # to the closing decision for runs recorded before fills carried their signal.
        signals = {
            (f["signal_ts"], f.get("signal_reason"))
            for f in exit_trace.get("fills") or []
            if f.get("signal_ts")
        } or {(exit_trace.get("ts"), exit_trace.get("reason"))}
        for signal_ts, reason in sorted(signals, key=lambda s: str(s[0])):
            closing = signal_ts == exit_trace.get("ts")
            g.mark(
                _dt(signal_ts),
                "EXIT_SIGNAL",
                f"Exit signal: {reason}"
                + (" (closed the position)" if closing and len(signals) > 1 else ""),
                _float(exit_trace.get("price")) if closing else None,
            )
        for check in exit_trace.get("checks") or []:
            if check.get("clause"):
                g.clause_windows([check["clause"]], "exit")
    elif decision == "SETTLE":
        g.mark(
            exit_ts,
            "SETTLE",
            f"Settled {exit_trace.get('resolution')}: pays {_num(exit_trace.get('payout'))}",
            _float(exit_trace.get("payout")),
        )
    elif decision == "END_OF_BACKTEST":
        g.mark(
            exit_ts,
            "END_OF_BACKTEST",
            f"End of backtest: valued at {_num(exit_trace.get('valued_at_mark'))}",
            _float(exit_trace.get("valued_at_mark")),
        )
    for fill in exit_trace.get("fills") or []:
        g.mark(
            _dt(fill.get("ts")),
            "EXIT_FILL",
            f"Exit fill {_num(fill.get('fill_price'))} × {_num(fill.get('shares'))}",
            _float(fill.get("fill_price")),
        )
    return g


def near_miss_geometry(nm: NearMissDetail) -> Geometry:
    g = Geometry()
    g.mark(
        nm.ts, "NEAR_MISS", f"No entry: {why_not(nm.blocked_by, nm.failed_clauses)}", None, nm.seq
    )
    g.clause_windows(nm.trace.get("clauses") or [], "entry")
    g.price_rules(nm.trace.get("clauses") or [])
    return g


def market(conn: psycopg.Connection[Any], condition_id: str, token: str) -> ReplayMarket:
    row = conn.execute(
        "SELECT m.question, o.outcome_name, m.tick_size, m.open_ts, m.end_date, m.resolved_at, "
        "m.resolution FROM markets m LEFT JOIN outcomes o ON o.clob_token_id = %s "
        "WHERE m.condition_id = %s",
        (token, condition_id),
    ).fetchone()
    q, outcome, tick, open_ts, end, resolved_at, resolution = row or (None,) * 7
    return ReplayMarket(
        condition_id=condition_id,
        clob_token_id=token,
        question=q,
        outcome_name=outcome,
        tick_size=None if tick is None else str(tick),
        open_ts=open_ts,
        end_date=end,
        resolved_at=resolved_at,
        resolution=resolution,
    )


def series(
    conn: psycopg.Connection[Any], token: str, start: datetime, end: datetime
) -> tuple[ReplaySeries, ReplayPrints]:
    rows = conn.execute(
        "(SELECT ts, price FROM price_bars WHERE clob_token_id = %(t)s AND ts < %(s)s "
        " ORDER BY ts DESC LIMIT 1) "
        "UNION ALL "
        "(SELECT ts, price FROM price_bars WHERE clob_token_id = %(t)s AND ts >= %(s)s "
        " AND ts <= %(e)s ORDER BY ts)",
        {"t": token, "s": start, "e": end},
    ).fetchall()
    ts = np.array([int(r[0].timestamp()) for r in rows], dtype=np.int64)
    price = np.array([float(r[1]) for r in rows])
    downsampled = ts.size > MAX_PRICE_POINTS
    if downsampled:
        keep = lttb_indices(ts.astype(np.float64), price, MAX_PRICE_POINTS)
        ts, price = ts[keep], price[keep]
    prices = ReplaySeries(
        ts=ts.tolist(), price=price.tolist(), total_points=len(rows), downsampled=downsampled
    )
    print_rows = conn.execute(
        "SELECT ts, price, size, side FROM trade_prints WHERE clob_token_id = %s "
        "AND ts >= %s AND ts <= %s ORDER BY ts LIMIT %s",
        (token, start, end, MAX_PRINTS + 1),
    ).fetchall()
    truncated = len(print_rows) > MAX_PRINTS
    print_rows = print_rows[:MAX_PRINTS]
    prints = ReplayPrints(
        ts=[int(r[0].timestamp()) for r in print_rows],
        price=[float(r[1]) for r in print_rows],
        size=[float(r[2]) for r in print_rows],
        side=[r[3] for r in print_rows],
        truncated=truncated,
    )
    return prices, prints
