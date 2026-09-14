"""Saved strategies and their versions (PRD §14): every edit is a new, immutable version."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, HTTPException, Query, status

from syntha_api.deps import ConnDep
from syntha_api.diff import diff
from syntha_contracts.api import (
    DiffEntry,
    FeatureInfo,
    RunSummary,
    SaveStrategyRequest,
    SaveVersionResponse,
    StrategyDetail,
    StrategyDiff,
    StrategySummary,
    StrategyVersionInfo,
)
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.strategy.compiler import compile_strategy
from syntha_engine.strategy.features import REGISTRY, FeatureError
from syntha_worker.runs import strategy_version

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/features")
def features() -> list[FeatureInfo]:
    """The feature registry the DSL can reference (PRD §8.2)."""
    return [
        FeatureInfo(
            name=s.name, params=list(s.params), definition=s.definition, null_when=s.null_when
        )
        for s in REGISTRY.values()
    ]


@router.get("")
def list_strategies(conn: ConnDep) -> list[StrategySummary]:
    rows = conn.execute(
        """SELECT s.id, s.name, s.created_at,
                  max(v.created_at), max(v.version), count(DISTINCT v.id),
                  (SELECT count(*) FROM backtest_runs r JOIN strategy_versions v2
                     ON v2.id = r.strategy_version_id WHERE v2.strategy_id = s.id),
                  (SELECT v3.dsl->>'description' FROM strategy_versions v3
                     WHERE v3.strategy_id = s.id ORDER BY v3.version DESC LIMIT 1)
           FROM strategies s JOIN strategy_versions v ON v.strategy_id = s.id
           GROUP BY s.id ORDER BY max(v.created_at) DESC"""
    ).fetchall()
    return [
        StrategySummary(
            id=str(r[0]),
            name=r[1],
            created_at=r[2],
            updated_at=r[3],
            latest_version=r[4],
            versions=r[5],
            runs=r[6],
            description=r[7],
        )
        for r in rows
    ]


def _run_summary(conn: psycopg.Connection[Any], run_id: str | None) -> RunSummary | None:
    if run_id is None:
        return None
    from syntha_api.routes.backtests import RUN_COLUMNS, _summary

    row = conn.execute(
        f"SELECT {RUN_COLUMNS} FROM backtest_runs WHERE id = %s", (run_id,)
    ).fetchone()
    return None if row is None else _summary(row)


def _versions(conn: psycopg.Connection[Any], strategy_id: uuid.UUID) -> list[StrategyVersionInfo]:
    rows = conn.execute(
        """SELECT v.id, v.version, v.dsl_hash, v.created_at, v.note, v.dsl,
                  (SELECT count(*) FROM backtest_runs r WHERE r.strategy_version_id = v.id),
                  (SELECT r.id FROM backtest_runs r WHERE r.strategy_version_id = v.id
                     ORDER BY r.created_at DESC LIMIT 1)
           FROM strategy_versions v WHERE v.strategy_id = %s ORDER BY v.version DESC""",
        (strategy_id,),
    ).fetchall()
    return [
        StrategyVersionInfo(
            id=str(r[0]),
            version=r[1],
            dsl_hash=r[2],
            created_at=r[3],
            note=r[4],
            dsl=StrategyDSL.model_validate(r[5]),
            runs=r[6],
            latest_run=_run_summary(conn, r[7]),
        )
        for r in rows
    ]


def _strategy_row(conn: psycopg.Connection[Any], strategy_id: uuid.UUID) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT id, name, created_at FROM strategies WHERE id = %s", (strategy_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"strategy {strategy_id} not found")
    return row  # type: ignore[no-any-return]


@router.get("/{strategy_id}")
def get_strategy(strategy_id: uuid.UUID, conn: ConnDep) -> StrategyDetail:
    row = _strategy_row(conn, strategy_id)
    return StrategyDetail(
        id=str(row[0]), name=row[1], created_at=row[2], versions=_versions(conn, strategy_id)
    )


def _compile(dsl: StrategyDSL) -> None:
    try:
        compile_strategy(dsl, 1)
    except FeatureError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _save(conn: psycopg.Connection[Any], dsl: StrategyDSL, note: str | None) -> SaveVersionResponse:
    _compile(dsl)
    existing = conn.execute(
        "SELECT v.id FROM strategy_versions v JOIN strategies s ON s.id = v.strategy_id "
        "WHERE s.name = %s AND v.dsl_hash = %s",
        (dsl.name, dsl.dsl_hash()),
    ).fetchone()
    version_id = strategy_version(conn, dsl, note)
    conn.commit()
    sid = conn.execute(
        "SELECT strategy_id FROM strategy_versions WHERE id = %s", (version_id,)
    ).fetchone()
    assert sid is not None
    info = next(v for v in _versions(conn, sid[0]) if v.id == str(version_id))
    return SaveVersionResponse(strategy_id=str(sid[0]), version=info, created=existing is None)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_strategy(body: SaveStrategyRequest, conn: ConnDep) -> SaveVersionResponse:
    """Save a strategy document. The name identifies the strategy: saving under an existing name
    adds a version to it (or returns the identical version)."""
    return _save(conn, body.dsl, body.note)


@router.post("/{strategy_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    strategy_id: uuid.UUID, body: SaveStrategyRequest, conn: ConnDep
) -> SaveVersionResponse:
    row = _strategy_row(conn, strategy_id)
    dsl = body.dsl.model_copy(update={"name": row[1]})  # a version never renames the strategy
    return _save(conn, dsl, body.note)


@router.get("/{strategy_id}/diff")
def diff_versions(
    strategy_id: uuid.UUID,
    conn: ConnDep,
    versions: Annotated[list[int], Query(min_length=2, max_length=6)],
) -> StrategyDiff:
    _strategy_row(conn, strategy_id)
    rows = dict(
        conn.execute(
            "SELECT version, dsl FROM strategy_versions WHERE strategy_id = %s AND version = ANY(%s)",
            (strategy_id, versions),
        ).fetchall()
    )
    missing = [v for v in versions if v not in rows]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"versions not found: {missing}")
    return StrategyDiff(
        strategy_id=str(strategy_id),
        versions=versions,
        changes=[DiffEntry(path=p, values=vals) for p, vals in diff([rows[v] for v in versions])],
    )
