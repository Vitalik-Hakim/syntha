"""Backtest runs as database records: create, execute, persist (PRD §7, §14).

Runs are immutable. A rerun with any change is a new run with a new id; results are never
updated in place. The API creates runs and the worker executes them; both use this module so
they share one definition of a run.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.builtins import builtin_strategy
from syntha_contracts.enums import RunStatus
from syntha_contracts.strategy import StrategyDSL
from syntha_engine import ENGINE_VERSION
from syntha_engine.data import Dataset, SettlementTable
from syntha_engine.honesty import compute_honesty
from syntha_engine.runner import BacktestResult, prepare, simulate
from syntha_engine.strategy.compiler import compile_strategy
from syntha_worker.loader import LoadedDataset, load_dataset

log = logging.getLogger(__name__)

PROGRESS_TTL_S = 86_400


class NoDatasetVersion(RuntimeError):
    """There is no dataset version to run against; `make rebuild-normalized` first."""


def plain_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def database_url() -> str:
    from syntha_storage.db import database_url as sa_url

    return plain_url(sa_url())


def _scalar(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError(f"query returned no row: {sql}")
    return row[0]


def progress_key(run_id: str) -> str:
    return f"syntha:run:{run_id}:progress"


# ------------------------------------------------------------------ create


def strategy_version(
    conn: psycopg.Connection[Any], dsl: StrategyDSL, note: str | None = None
) -> uuid.UUID:
    """Find or create the strategy version for this DSL (by name + dsl_hash). Versions number
    1, 2, … per strategy name; an identical document reuses its version."""
    digest = dsl.dsl_hash()
    row = conn.execute(
        "SELECT v.id FROM strategy_versions v JOIN strategies s ON s.id = v.strategy_id "
        "WHERE s.name = %s AND v.dsl_hash = %s",
        (dsl.name, digest),
    ).fetchone()
    if row:
        return uuid.UUID(str(row[0]))
    strategy = conn.execute(
        "SELECT id FROM strategies WHERE name = %s ORDER BY created_at LIMIT 1", (dsl.name,)
    ).fetchone()
    if strategy is None:
        strategy_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO strategies (id, name, created_at) VALUES (%s, %s, now())",
            (strategy_id, dsl.name),
        )
        version = 1
    else:
        strategy_id = uuid.UUID(str(strategy[0]))
        version = int(
            _scalar(
                conn,
                "SELECT coalesce(max(version), 0) + 1 FROM strategy_versions WHERE strategy_id = %s",
                (strategy_id,),
            )
        )
    version_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO strategy_versions (id, strategy_id, version, dsl, dsl_hash, created_at, note) "
        "VALUES (%s, %s, %s, %s, %s, now(), %s)",
        (version_id, strategy_id, version, Jsonb(dsl.model_dump(mode="json")), digest, note),
    )
    return version_id


def create_run(
    conn: psycopg.Connection[Any],
    request: BacktestRequest,
    group_id: uuid.UUID | None = None,
    parent_run_id: str | None = None,
    note: str | None = None,
) -> str:
    """Validate and insert a QUEUED run. Returns its id (BT-YYYYMMDD-NNNNN). Runs of one "run all
    three presets" request share `group_id`."""
    compile_strategy(request.strategy, request.bar_interval_minutes)  # raises on invalid features
    dataset = conn.execute(
        "SELECT id FROM dataset_versions ORDER BY built_at DESC LIMIT 1"
    ).fetchone()
    if dataset is None:
        raise NoDatasetVersion("no dataset version; run `make rebuild-normalized` first")
    version_id = strategy_version(conn, request.strategy, note)
    seq = int(_scalar(conn, "SELECT nextval('backtest_run_seq')"))
    run_id = f"BT-{datetime.now(UTC):%Y%m%d}-{seq:05d}"
    conn.execute(
        """INSERT INTO backtest_runs (id, strategy_version_id, dataset_version_id, config, config_hash,
             engine_version, seed, status, data_quality, strategy_name, collection, group_id, preset,
             parent_run_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, %s, %s, %s, %s, %s)""",
        (
            run_id,
            version_id,
            dataset[0],
            Jsonb(request.model_dump(mode="json")),
            request.config_hash(),
            ENGINE_VERSION,
            request.seed,
            RunStatus.QUEUED.value,
            request.strategy.name,
            request.dataset.collection,
            group_id,
            request.preset_name(),
            parent_run_id,
        ),
    )
    return run_id


# ------------------------------------------------------------------ persist


def _equity_changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first and last rows and every row where the portfolio state changed. The curve
    is a step function between kept rows, so nothing is lost."""
    kept: list[dict[str, Any]] = []
    last_key: tuple[Any, ...] | None = None
    for i, row in enumerate(rows):
        key = (row["cash"], row["positions_value"], row["open_positions"], row["realized_pnl"])
        if key != last_key or i == len(rows) - 1:
            kept.append(row)
            last_key = key
    return kept


def persist_result(conn: psycopg.Connection[Any], run_id: str, result: BacktestResult) -> None:
    with conn.cursor() as cur:
        with cur.copy(
            "COPY bt_trades (run_id, seq, condition_id, clob_token_id, side, signal_ts, entry_ts, "
            "entry_price, entry_ref_price, shares, notional, entry_fee, exit_ts, exit_price, exit_fee, "
            "exit_reason, gross_pnl, fees_total, slippage_cost, net_pnl, return_pct, holding_seconds, "
            "entry_rule_trace, exit_rule_trace, fill_status) FROM STDIN"
        ) as cp:
            for t in result.trades:
                cp.write_row(
                    (
                        run_id,
                        t["seq"],
                        t["condition_id"],
                        t["clob_token_id"],
                        t["side"],
                        t["signal_ts"],
                        t["entry_ts"],
                        t["entry_price"],
                        t["entry_ref_price"],
                        t["shares"],
                        t["notional"],
                        t["entry_fee"],
                        t["exit_ts"],
                        t["exit_price"],
                        t["exit_fee"],
                        t["exit_reason"],
                        t["gross_pnl"],
                        t["fees_total"],
                        t["slippage_cost"],
                        t["net_pnl"],
                        t["return_pct"],
                        t["holding_seconds"],
                        Jsonb(t["entry_rule_trace"]),
                        None if t["exit_rule_trace"] is None else Jsonb(t["exit_rule_trace"]),
                        t["fill_status"],
                    )
                )
        with cur.copy(
            "COPY bt_equity (run_id, ts, cash, positions_value, equity, open_positions, realized_pnl, "
            "unrealized_pnl, exposure_pct) FROM STDIN"
        ) as cp:
            for e in _equity_changes(result.equity):
                cp.write_row(
                    (
                        run_id,
                        e["ts"],
                        e["cash"],
                        e["positions_value"],
                        e["equity"],
                        e["open_positions"],
                        e["realized_pnl"],
                        e["unrealized_pnl"],
                        e["exposure_pct"],
                    )
                )
        with cur.copy(
            "COPY bt_ledger (run_id, seq, ts, type, amount, running_cash, ref) FROM STDIN"
        ) as cp:
            for i, entry in enumerate(result.ledger):
                cp.write_row(
                    (
                        run_id,
                        i,
                        entry["ts"],
                        entry["type"],
                        entry["amount"],
                        entry["running_cash"],
                        entry["ref"],
                    )
                )
        with cur.copy(
            "COPY bt_near_misses (run_id, seq, condition_id, clob_token_id, ts, kind, score, "
            "failed_clauses, blocked_by, trace) FROM STDIN"
        ) as cp:
            for nm in result.near_misses:
                cp.write_row(
                    (
                        run_id,
                        nm["seq"],
                        nm["condition_id"],
                        nm["clob_token_id"],
                        nm["ts"],
                        nm["kind"],
                        None if nm["score"] is None else str(nm["score"]),
                        nm["failed_clauses"],
                        nm["blocked_by"],
                        Jsonb(nm["trace"]),
                    )
                )


# ------------------------------------------------------------------ execute


def backtest_with_honesty(
    request: BacktestRequest,
    dataset: Dataset,
    settlement: SettlementTable,
    progress: Callable[[str, float], None] | None = None,
) -> tuple[BacktestResult, dict[str, Any]]:
    """The backtest, then its null-hypothesis tests over the same preparation (PRD §15)."""
    prep = prepare(request, dataset, settlement, progress=progress)
    result = simulate(prep, progress=progress)
    assert isinstance(result, BacktestResult)
    honesty = compute_honesty(
        prep,
        result,
        buy_and_hold_dsl=builtin_strategy("buy_and_hold_to_resolution"),
        progress=progress,
    )
    return result, honesty


def execute_run(
    run_id: str,
    db_url: str | None = None,
    on_progress: Callable[[str, float], None] | None = None,
    loaded: LoadedDataset | None = None,
) -> RunStatus:
    """Run a QUEUED backtest to completion. Failures are recorded on the run, never swallowed.
    `loaded` lets a batch of runs over the same markets share one load (research scripts)."""
    url = db_url or database_url()
    report = on_progress or (lambda phase, fraction: None)
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT config, status FROM backtest_runs WHERE id = %s", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row[1] != RunStatus.QUEUED.value:
            log.warning("run %s is %s, not QUEUED; skipping", run_id, row[1])
            return RunStatus(row[1])
        conn.execute(
            "UPDATE backtest_runs SET status = 'RUNNING', started_at = now() WHERE id = %s",
            (run_id,),
        )
        conn.commit()
    try:
        request = BacktestRequest.model_validate(row[0])
        report("load", 0.0)
        if loaded is None:
            loaded = load_dataset(url, request)
        result, honesty = backtest_with_honesty(
            request, loaded.dataset, loaded.settlement, progress=report
        )
        report("persist", 0.0)
        result.manifest["run_id"] = run_id
        with psycopg.connect(url) as conn, conn.transaction():
            persist_result(conn, run_id, result)
            conn.execute(
                "UPDATE backtest_runs SET status = 'DONE', finished_at = now(), metrics = %s, "
                "data_quality = %s, manifest = %s, honesty = %s WHERE id = %s",
                (
                    Jsonb(json.loads(json.dumps(result.metrics, default=str))),
                    Jsonb(json.loads(json.dumps(result.data_quality, default=str))),
                    Jsonb(result.manifest),
                    Jsonb(json.loads(json.dumps(honesty, default=str))),
                    run_id,
                ),
            )
        report("done", 1.0)
        return RunStatus.DONE
    except Exception as exc:
        log.exception("run %s failed", run_id)
        with psycopg.connect(url) as conn:
            conn.execute(
                "UPDATE backtest_runs SET status = 'FAILED', finished_at = now(), error = %s WHERE id = %s",
                (f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-4000:]}", run_id),
            )
        report("failed", 1.0)
        return RunStatus.FAILED


def rq_job(run_id: str) -> str:
    """Entry point the RQ worker calls. Publishes progress to Redis for the SSE stream."""
    import redis

    client = redis.Redis.from_url(os.environ.get("SYNTHA_REDIS_URL", "redis://localhost:6379/0"))
    last: dict[str, Any] = {"phase": "", "at": 0.0}

    def publish(phase: str, fraction: float) -> None:
        now = datetime.now(UTC)
        if phase == last["phase"] and now.timestamp() - float(last["at"]) < 0.5 and fraction < 1.0:
            return
        last.update(phase=phase, at=now.timestamp())
        client.set(
            progress_key(run_id),
            json.dumps(
                {"phase": phase, "fraction": round(fraction, 4), "updated_at": now.isoformat()}
            ),
            ex=PROGRESS_TTL_S,
        )

    return execute_run(run_id, on_progress=publish).value
