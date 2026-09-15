"""Build the engine's in-memory Dataset from the normalized tables.

This is the boundary PRD §7 describes: ENGINE-RESTRICTED columns (resolution, resolved_at,
closed_time, final_volume) are read here and placed only in the SettlementTable, never in the
market data a strategy can reach.

Only the tokens the strategy can trade (its universe side) and the requested window are loaded.
Bulk rows come through `COPY … TO STDOUT` parsed by Polars; row-by-row fetches of tens of
millions of bars would take minutes.
"""

from __future__ import annotations

import io
import itertools
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import numpy as np
import polars as pl
import psycopg

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.enums import Side
from syntha_engine.data import (
    Dataset,
    FeeScheduleEntry,
    MarketTerms,
    Settlement,
    SettlementTable,
    TokenData,
)

log = logging.getLogger(__name__)

# v3 §4.2 catch-all *candidates*: a neg-risk market whose question reads like the residual
# outcome, or one Gamma flags as `negRiskOther`. Candidates are listed for review by
# tools/research/catch_all_review.py; only a reviewed verdict (catch_all_reviews) reaches the
# engine (M9 decision, docs/DECISIONS.md). A false "catch-all present" would let a basket treat a
# non-exhaustive set as exhaustive, so nothing is inferred automatically.
CATCH_ALL = re.compile(
    r"^\s*(other|another|someone else|anyone else|none of the above|no one|nobody)\b"
    r"|\bwill (another|any other|some other|someone else|anyone else|no one|nobody)\b"
    r"|\b(none of the above|any other (candidate|team|player|person|country|outcome))\b",
    re.IGNORECASE,
)


def is_catch_all_candidate(
    question: str | None, neg_risk: bool | None, neg_risk_other: bool | None
) -> bool:
    if not neg_risk:
        return False
    return bool(neg_risk_other) or bool(question and CATCH_ALL.search(question))


@dataclass(frozen=True)
class LoadedDataset:
    dataset: Dataset
    settlement: SettlementTable
    content_hash: str | None


def _epoch(value: Any) -> int | None:
    return None if value is None else int(value.timestamp())


def _copy_frame(
    conn: psycopg.Connection[Any], sql: str, params: dict[str, Any], schema: dict[str, Any]
) -> pl.DataFrame:
    query = psycopg.ClientCursor(conn).mogrify(sql, params)
    buf = io.BytesIO()
    with conn.cursor() as cur, cur.copy(f"COPY ({query}) TO STDOUT (FORMAT csv)") as cp:
        for chunk in cp:
            buf.write(chunk)
    if buf.tell() == 0:
        return pl.DataFrame(schema=schema)
    buf.seek(0)
    return pl.read_csv(buf, has_header=False, new_columns=list(schema), schema_overrides=schema)


def _split(df: pl.DataFrame, cols: list[str], names: list[str]) -> dict[str, dict[str, np.ndarray]]:
    """Split rows keyed by 1-based token position (array_position) into per-token arrays."""
    if df.is_empty():
        return {}
    df = df.sort(["token", "ts"])
    tokens = df["token"].to_numpy()
    arrays = {c: df[c].to_numpy() for c in cols}
    bounds = np.flatnonzero(np.r_[True, tokens[1:] != tokens[:-1], True])
    out = {}
    for a, b in itertools.pairwise(bounds):
        out[names[int(tokens[a]) - 1]] = {c: arrays[c][a:b].copy() for c in cols}
    return out


def coverage_window(
    selection: dict[str, Any] | None, start: Any, end: Any
) -> tuple[int, int] | None:
    """The collection's scheduled-end window, narrowed by an explicit request window (M9
    decision: capacity per month is measured over it). None without a collection window."""
    if not selection or not selection.get("end_date_min") or not selection.get("end_date_max"):
        return None
    lo = datetime.fromisoformat(str(selection["end_date_min"]).replace("Z", "+00:00"))
    hi = datetime.fromisoformat(str(selection["end_date_max"]).replace("Z", "+00:00"))
    if start is not None:
        lo = max(lo, start)
    if end is not None:
        hi = min(hi, end)
    if hi <= lo:
        return None
    return int(lo.timestamp()), int(hi.timestamp())


def load_dataset(database_url: str, request: BacktestRequest) -> LoadedDataset:
    sel = request.dataset
    universe = request.strategy.universe
    outcome_indexes = {Side.BUY_YES: [0], Side.BUY_NO: [1], Side.EITHER: [0, 1]}[universe.side]
    params: dict[str, Any] = {
        "collection": sel.collection,
        "conditions": sel.condition_ids,
        "categories": [c.lower() for c in universe.categories] if universe.categories else None,
        "start": sel.start,
        "end": sel.end,
        "outcome_indexes": outcome_indexes,
    }
    market_filter = """
        FROM markets m
        WHERE (%(collection)s::text IS NULL OR m.condition_id IN (
                 SELECT cm.condition_id FROM collection_markets cm
                 JOIN collections c ON c.id = cm.collection_id WHERE c.name = %(collection)s))
          AND (%(conditions)s::text[] IS NULL OR m.condition_id = ANY(%(conditions)s::text[]))
          AND (%(categories)s::text[] IS NULL OR lower(m.category) = ANY(%(categories)s::text[]))
    """
    with psycopg.connect(database_url) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        version = conn.execute(
            "SELECT id, content_hash FROM dataset_versions ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
        selection: dict[str, Any] | None = None
        if sel.collection is not None:
            exists = conn.execute(
                "SELECT definition FROM collections WHERE name = %s", (sel.collection,)
            ).fetchone()
            if exists is not None:
                selection = (exists[0] or {}).get("definition")
            if exists is None:
                raise ValueError(
                    f"collection {sel.collection!r} not found; run `make rebuild-normalized`"
                )

        rows = conn.execute(
            "SELECT m.condition_id, m.event_id, m.category, m.tick_size, m.min_order_size, m.created_at, "
            "m.open_ts, m.end_date, m.fees_enabled, m.fee_type, m.fee_rate, m.fee_exponent, "
            "m.resolution, m.resolved_at, m.closed_time, m.neg_risk, r.is_catch_all "
            + market_filter.replace(
                "FROM markets m",
                "FROM markets m LEFT JOIN catch_all_reviews r ON r.condition_id = m.condition_id",
            )
            + " ORDER BY m.condition_id",
            params,
        ).fetchall()
        markets: dict[str, MarketTerms] = {}
        settlements: dict[str, Settlement] = {}
        for r in rows:
            markets[r[0]] = MarketTerms(
                condition_id=r[0],
                event_id=r[1],
                category=r[2],
                tick_size=Decimal(r[3]),
                min_order_size=None if r[4] is None else Decimal(r[4]),
                created_ts=_epoch(r[5]),
                open_ts=_epoch(r[6]),
                end_ts=_epoch(r[7]),
                fees_enabled=r[8],
                fee_type=r[9],
                fee_rate=None if r[10] is None else Decimal(r[10]),
                fee_exponent=r[11],
                neg_risk=r[15],
                is_catch_all=r[16],  # a reviewed verdict or None; never inferred here
            )
            # ENGINE-RESTRICTED columns go only to settlement.
            settlements[r[0]] = Settlement(r[12] or "UNRESOLVED", _epoch(r[13]), _epoch(r[14]))

        outcomes = conn.execute(
            "SELECT o.clob_token_id, o.condition_id, o.outcome_index, o.outcome_name FROM outcomes o "
            "WHERE o.condition_id = ANY(%(ids)s) AND o.outcome_index = ANY(%(idx)s) ORDER BY 1",
            {"ids": list(markets), "idx": outcome_indexes},
        ).fetchall()
        token_ids = [o[0] for o in outcomes]
        window = {"tokens": token_ids, "start": sel.start, "end": sel.end}
        log.info("loading %d markets / %d tokens", len(markets), len(token_ids))

        bars = _copy_frame(
            conn,
            """
            SELECT t.idx, extract(epoch FROM b.ts)::bigint, b.price
            FROM price_bars b
            JOIN unnest(%(tokens)s::text[]) WITH ORDINALITY AS t(token, idx) ON b.clob_token_id = t.token
            WHERE TRUE
              AND (%(start)s::timestamptz IS NULL OR b.ts >= %(start)s)
              AND (%(end)s::timestamptz IS NULL OR b.ts <= %(end)s)
        """,
            window,
            {"token": pl.Int32, "ts": pl.Int64, "price": pl.Float64},
        )
        prints = _copy_frame(
            conn,
            """
            SELECT t.idx, extract(epoch FROM p.ts)::bigint, p.price, p.size,
                   CASE p.side WHEN 'BUY' THEN 1 ELSE -1 END, p.proxy_wallet
            FROM trade_prints p
            JOIN unnest(%(tokens)s::text[]) WITH ORDINALITY AS t(token, idx) ON p.clob_token_id = t.token
            WHERE TRUE
              AND (%(start)s::timestamptz IS NULL OR p.ts >= %(start)s)
              AND (%(end)s::timestamptz IS NULL OR p.ts <= %(end)s)
        """,
            window,
            {
                "token": pl.Int32,
                "ts": pl.Int64,
                "price": pl.Float64,
                "size": pl.Float64,
                "side": pl.Int8,
                "wallet": pl.Utf8,
            },
        )
        prior: dict[str, float] = dict(
            conn.execute(
                """
            SELECT clob_token_id, sum(price * size)::float8 FROM trade_prints
            WHERE clob_token_id = ANY(%(tokens)s) AND %(start)s::timestamptz IS NOT NULL AND ts < %(start)s
            GROUP BY 1
        """,
                window,
            ).fetchall()
        )

        schedules = tuple(
            FeeScheduleEntry(
                fee_type=r[0],
                rate=Decimal(r[1]),
                exponent=r[2],
                applies_to=r[3],
                effective_from=int(r[4].timestamp()),
                effective_to=_epoch(r[5]),
            )
            for r in conn.execute(
                "SELECT fee_type, rate, exponent, applies_to, effective_from, effective_to FROM fee_schedules "
                "WHERE venue = 'international' ORDER BY fee_type, effective_from"
            ).fetchall()
        )

    bar_arrays = _split(bars, ["ts", "price"], token_ids)
    del bars
    # v3 E4: wallets become small integer ids in order of first appearance; -1 = unknown
    cat = prints["wallet"].cast(pl.Categorical)
    wallet_names = tuple(str(w) for w in cat.cat.get_categories().to_list())
    prints = prints.with_columns(cat.to_physical().cast(pl.Int32).fill_null(-1).alias("wallet_id"))
    print_arrays = _split(prints, ["ts", "price", "size", "side", "wallet_id"], token_ids)
    del prints
    empty_i, empty_f = np.empty(0, np.int64), np.empty(0, np.float64)
    tokens: dict[str, TokenData] = {}
    for tok, cid, idx, name in outcomes:
        b = bar_arrays.get(tok, {})
        p = print_arrays.get(tok, {})
        tokens[tok] = TokenData(
            clob_token_id=tok,
            condition_id=cid,
            outcome_index=idx,
            outcome_name=name,
            bar_ts=b.get("ts", empty_i).astype(np.int64),
            bar_price=b.get("price", empty_f).astype(np.float64),
            print_ts=p.get("ts", empty_i).astype(np.int64),
            print_price=p.get("price", empty_f).astype(np.float64),
            print_size=p.get("size", empty_f).astype(np.float64),
            print_side=p.get("side", np.empty(0, np.int8)).astype(np.int8),
            prior_notional=float(prior.get(tok) or 0.0),
            print_wallet=p.get("wallet_id", np.empty(0, np.int32)).astype(np.int32)
            if wallet_names
            else None,
        )
    dataset = Dataset(
        markets=markets,
        tokens=tokens,
        fee_schedules=schedules,
        dataset_version_id=None if version is None else str(version[0]),
        selection=selection,
        wallets=wallet_names,
        coverage=coverage_window(selection, sel.start, sel.end),
    )
    return LoadedDataset(
        dataset, SettlementTable(settlements), None if version is None else version[1]
    )
