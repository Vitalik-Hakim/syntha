"""Backtest runs (PRD §14). Runs are immutable; every number returned comes from backtest_runs,
bt_trades or bt_equity (PRD §0 rule 3)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Annotated, Any

import numpy as np
import psycopg
import redis
from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from syntha_api.deps import ConnDep, SettingsDep
from syntha_api.lttb import equity_indices
from syntha_api.settings import Settings
from syntha_contracts.api import (
    CreateBacktestResponse,
    EquityPoint,
    EquitySeries,
    RunDetail,
    RunList,
    RunProgress,
    RunSummary,
    TradeDetail,
    TradePage,
    TradeRow,
)
from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.enums import RunStatus
from syntha_engine.strategy.features import FeatureError
from syntha_worker import BACKTEST_QUEUE
from syntha_worker.runs import NoDatasetVersion, create_run, execute_run, progress_key

router = APIRouter(prefix="/backtests", tags=["backtests"])

RUN_COLUMNS = """id, status, strategy_name, collection, created_at, started_at, finished_at, config,
    config_hash, engine_version, dataset_version_id, seed, error, metrics, data_quality, manifest"""

TRADE_COLUMNS = """t.seq, t.fill_status, t.condition_id, t.clob_token_id, t.side, t.signal_ts, t.entry_ts,
    t.entry_ref_price, t.entry_price, t.shares, t.notional, t.entry_fee, t.exit_ts, t.exit_price, t.exit_fee,
    t.exit_reason, t.gross_pnl, t.fees_total, t.slippage_cost, t.net_pnl, t.return_pct, t.holding_seconds,
    (t.entry_rule_trace->>'attempts')::int, t.entry_rule_trace->'execution'->>'reason', m.question"""


def _num(value: Any) -> str | None:
    return None if value is None else str(value)


def _summary(row: tuple[Any, ...]) -> RunSummary:
    (run_id, st, strategy_name, collection, created, started, finished, config, *_rest) = row
    metrics, dq = row[13] or {}, row[14] or {}
    return RunSummary(
        id=run_id,
        status=RunStatus(st),
        strategy_name=strategy_name,
        collection=collection,
        created_at=created,
        started_at=started,
        finished_at=finished,
        execution_model=(config or {}).get("execution", {}).get("model"),
        net_profit=metrics.get("net_profit"),
        roi=metrics.get("roi"),
        n_trades=metrics.get("n_trades"),
        signals_generated=dq.get("signals_generated"),
        signals_filled=dq.get("signals_filled"),
        warnings=metrics.get("warnings", []),
    )


def _progress(settings: Settings, run_id: str) -> RunProgress | None:
    try:
        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        raw = client.get(progress_key(run_id))
    except redis.RedisError:
        return None
    if not raw:
        return None
    return RunProgress.model_validate(json.loads(raw))


def _fetch_run(conn: psycopg.Connection[Any], run_id: str) -> tuple[Any, ...]:
    row = conn.execute(
        f"SELECT {RUN_COLUMNS} FROM backtest_runs WHERE id = %s", (run_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
    return row  # type: ignore[no-any-return]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_backtest(
    request: BacktestRequest, conn: ConnDep, settings: SettingsDep
) -> CreateBacktestResponse:
    if request.dataset.collection is not None:
        found = conn.execute(
            "SELECT 1 FROM collections WHERE name = %s", (request.dataset.collection,)
        ).fetchone()
        if found is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"collection {request.dataset.collection!r} not found"
            )
    try:
        run_id = create_run(conn, request)
    except FeatureError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except NoDatasetVersion as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    conn.commit()

    if settings.queue_mode == "inline":
        final = execute_run(run_id, settings.plain_database_url)
        return CreateBacktestResponse(run_id=run_id, status=final)
    from rq import Queue

    queue = Queue(BACKTEST_QUEUE, connection=redis.Redis.from_url(settings.redis_url))
    queue.enqueue("syntha_worker.runs.rq_job", run_id, job_timeout=6 * 3600, result_ttl=86_400)
    return CreateBacktestResponse(run_id=run_id, status=RunStatus.QUEUED)


@router.get("")
def list_backtests(conn: ConnDep, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> RunList:
    rows = conn.execute(
        f"SELECT {RUN_COLUMNS} FROM backtest_runs ORDER BY created_at DESC LIMIT %s", (limit,)
    ).fetchall()
    return RunList(runs=[_summary(r) for r in rows])


@router.get("/{run_id}")
def get_backtest(run_id: str, conn: ConnDep, settings: SettingsDep) -> RunDetail:
    row = _fetch_run(conn, run_id)
    summary = _summary(row)
    return RunDetail(
        **summary.model_dump(),
        config=BacktestRequest.model_validate(row[7]),
        config_hash=row[8],
        engine_version=row[9],
        dataset_version_id=str(row[10]),
        seed=row[11],
        error=row[12],
        progress=_progress(settings, run_id)
        if summary.status in (RunStatus.QUEUED, RunStatus.RUNNING)
        else None,
        metrics=row[13],
        data_quality=row[14] or None,
        manifest=row[15],
    )


@router.get("/{run_id}/stream")
async def stream_backtest(run_id: str, settings: SettingsDep) -> StreamingResponse:
    """Server-sent events: `progress` while queued or running, then one `status` event."""

    def poll() -> tuple[str, RunProgress | None]:
        with psycopg.connect(settings.plain_database_url) as conn:
            row = conn.execute(
                "SELECT status FROM backtest_runs WHERE id = %s", (run_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} not found")
        return row[0], _progress(settings, run_id)

    first_status, _ = await run_in_threadpool(poll)

    async def events() -> AsyncIterator[str]:
        last = None
        while True:
            st, prog = await run_in_threadpool(poll)
            payload = {"status": st, "progress": prog.model_dump(mode="json") if prog else None}
            if payload != last:
                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                last = payload
            if st in (RunStatus.DONE.value, RunStatus.FAILED.value):
                yield f"event: status\ndata: {json.dumps({'status': st})}\n\n"
                return
            await asyncio.sleep(0.5)

    del first_status
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/equity")
def equity(
    run_id: str,
    conn: ConnDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    max_points: Annotated[int, Query(ge=10, le=20_000)] = 2000,
) -> EquitySeries:
    _fetch_run(conn, run_id)
    rows = conn.execute(
        "SELECT ts, equity, cash, positions_value, open_positions FROM bt_equity WHERE run_id = %s ORDER BY ts",
        (run_id,),
    ).fetchall()
    if not rows:
        return EquitySeries(
            run_id=run_id, total_points=0, returned_points=0, downsampled=False, points=[]
        )
    eq = np.array([float(r[1]) for r in rows])
    # Drawdown over the whole stored curve (stored rows are every change point, so the running
    # peak is exact), before any time filter or downsampling.
    peak = np.maximum.accumulate(eq)
    dd = np.where(peak > 0, 1 - eq / peak, 0.0)
    idx = np.arange(len(rows))
    if from_ is not None or to is not None:
        ts = np.array([r[0].timestamp() for r in rows])
        mask = np.ones(len(rows), dtype=bool)
        if from_ is not None:
            mask &= ts >= from_.timestamp()
        if to is not None:
            mask &= ts <= to.timestamp()
        idx = idx[mask]
    keep = idx
    if idx.size > max_points:
        x = np.array([rows[i][0].timestamp() for i in idx])
        keep = idx[equity_indices(x, eq[idx], dd[idx], max_points)]
    points = [
        EquityPoint(
            ts=rows[i][0],
            equity=_num(rows[i][1]) or "0",
            cash=_num(rows[i][2]) or "0",
            positions_value=_num(rows[i][3]) or "0",
            open_positions=rows[i][4],
            drawdown_pct=float(dd[i]),
        )
        for i in keep
    ]
    return EquitySeries(
        run_id=run_id,
        total_points=int(idx.size),
        returned_points=len(points),
        downsampled=bool(idx.size > max_points),
        points=points,
    )


def _trade_row(r: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "seq": r[0],
        "fill_status": r[1],
        "condition_id": r[2],
        "clob_token_id": r[3],
        "side": r[4],
        "signal_ts": r[5],
        "entry_ts": r[6],
        "entry_ref_price": _num(r[7]),
        "entry_price": _num(r[8]),
        "shares": _num(r[9]),
        "notional": _num(r[10]),
        "entry_fee": _num(r[11]),
        "exit_ts": r[12],
        "exit_price": _num(r[13]),
        "exit_fee": _num(r[14]),
        "exit_reason": r[15],
        "gross_pnl": _num(r[16]),
        "fees_total": _num(r[17]),
        "slippage_cost": _num(r[18]),
        "net_pnl": _num(r[19]),
        "return_pct": _num(r[20]),
        "holding_seconds": r[21],
        "attempts": r[22],
        "refusal_reason": r[23],
        "question": r[24],
    }


@router.get("/{run_id}/trades")
def trades(
    run_id: str,
    conn: ConnDep,
    fill_status: str | None = None,
    exit_reason: str | None = None,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TradePage:
    _fetch_run(conn, run_id)
    where = ["t.run_id = %(run)s"]
    params: dict[str, Any] = {"run": run_id, "limit": limit + 1}
    if fill_status:
        where.append("t.fill_status = %(fs)s")
        params["fs"] = fill_status
    if exit_reason:
        where.append("t.exit_reason = %(er)s")
        params["er"] = exit_reason
    filtered = " AND ".join(where)
    if cursor is not None:
        params["cursor"] = cursor
    rows = conn.execute(
        f"SELECT {TRADE_COLUMNS} FROM bt_trades t LEFT JOIN markets m ON m.condition_id = t.condition_id "
        f"WHERE {filtered}{' AND t.seq > %(cursor)s' if cursor is not None else ''} ORDER BY t.seq LIMIT %(limit)s",
        params,
    ).fetchall()
    total = conn.execute(f"SELECT count(*) FROM bt_trades t WHERE {filtered}", params).fetchone()
    counts = dict(
        conn.execute(
            "SELECT fill_status, count(*) FROM bt_trades WHERE run_id = %s GROUP BY 1", (run_id,)
        ).fetchall()
    )
    page = rows[:limit]
    return TradePage(
        run_id=run_id,
        trades=[TradeRow(**_trade_row(r)) for r in page],
        next_cursor=page[-1][0] if len(rows) > limit else None,
        total=int(total[0]) if total else 0,
        counts_by_status=counts,
    )


@router.get("/{run_id}/trades/{seq}")
def trade(run_id: str, seq: int, conn: ConnDep) -> TradeDetail:
    row = conn.execute(
        f"SELECT {TRADE_COLUMNS}, t.entry_rule_trace, t.exit_rule_trace FROM bt_trades t "
        "LEFT JOIN markets m ON m.condition_id = t.condition_id WHERE t.run_id = %s AND t.seq = %s",
        (run_id, seq),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"trade {seq} not found in {run_id}")
    return TradeDetail(**_trade_row(row), entry_rule_trace=row[25], exit_rule_trace=row[26])


@router.get("/{run_id}/export.csv", response_class=Response)
def export_csv(run_id: str, conn: ConnDep) -> StreamingResponse:
    _fetch_run(conn, run_id)
    rows = conn.execute(
        f"SELECT {TRADE_COLUMNS} FROM bt_trades t LEFT JOIN markets m ON m.condition_id = t.condition_id "
        "WHERE t.run_id = %s ORDER BY t.seq",
        (run_id,),
    ).fetchall()

    def body() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(TradeRow.model_fields))
        writer.writeheader()
        for r in rows:
            writer.writerow(_trade_row(r))
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
        yield buf.getvalue()

    return StreamingResponse(
        body(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{run_id}-trades.csv"'},
    )


@router.get("/{run_id}/export.json")
def export_json(run_id: str, conn: ConnDep, settings: SettingsDep) -> dict[str, Any]:
    detail = get_backtest(run_id, conn, settings)
    trade_rows = conn.execute(
        f"SELECT {TRADE_COLUMNS}, t.entry_rule_trace, t.exit_rule_trace FROM bt_trades t "
        "LEFT JOIN markets m ON m.condition_id = t.condition_id WHERE t.run_id = %s ORDER BY t.seq",
        (run_id,),
    ).fetchall()
    ledger = conn.execute(
        "SELECT seq, ts, type, amount, running_cash, ref FROM bt_ledger WHERE run_id = %s "
        "ORDER BY seq",
        (run_id,),
    ).fetchall()
    return {
        "run": detail.model_dump(mode="json"),
        "trades": [
            TradeDetail(**_trade_row(r), entry_rule_trace=r[25], exit_rule_trace=r[26]).model_dump(
                mode="json"
            )
            for r in trade_rows
        ],
        "ledger": [
            {
                "seq": r[0],
                "ts": r[1].isoformat(),
                "type": r[2],
                "amount": str(r[3]),
                "running_cash": str(r[4]),
                "ref": r[5],
            }
            for r in ledger
        ],
    }
