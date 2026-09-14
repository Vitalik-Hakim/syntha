"""Strategies, presets, collections and dataset versions: what a run can be built from."""

from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError

from syntha_api.deps import ConnDep
from syntha_contracts.api import (
    BuiltinStrategy,
    CollectionInfo,
    DatasetVersionInfo,
    PresetInfo,
    ValidateStrategyRequest,
    ValidationResult,
)
from syntha_contracts.backtest import ExecutionConfig, FeeConfig
from syntha_contracts.builtins import BUILTIN_STRATEGY_FILES, builtin_strategy
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.strategy.compiler import compile_strategy
from syntha_engine.strategy.features import FeatureError

router = APIRouter(tags=["catalog"])

PRESET_TEXT = {
    "OPTIMISTIC": (
        "Optimistic",
        "Every signal fills at the sampled price with unlimited size and no slippage. "
        "Optimistic about execution, not a forecast of P&L; never read alone.",
    ),
    "REALISTIC": (
        "Realistic",
        "Fills against trade prints in the next bar at VWAP plus one tick, taking at "
        "most 10% of printed size. The default.",
    ),
    "CONSERVATIVE": (
        "Conservative",
        "Worst print in the bar plus two ticks, at most 5% of printed size, "
        "partial fills refused, today's fee schedule.",
    ),
}


@router.get("/strategies/builtins")
def builtins() -> list[BuiltinStrategy]:
    out = []
    for key in BUILTIN_STRATEGY_FILES:
        dsl = builtin_strategy(key)
        out.append(BuiltinStrategy(key=key, name=dsl.name, description=dsl.description, dsl=dsl))
    return out


@router.post("/strategies/validate")
def validate(body: ValidateStrategyRequest) -> ValidationResult:
    try:
        dsl = StrategyDSL.model_validate(body.dsl)
        compiled = compile_strategy(dsl, body.bar_interval_minutes)
    except ValidationError as exc:
        return ValidationResult(
            ok=False, errors=[f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        )
    except FeatureError as exc:
        return ValidationResult(ok=False, errors=[str(exc)])
    return ValidationResult(
        ok=True,
        dsl_hash=dsl.dsl_hash(),
        features=[r.key for r in compiled.feature_refs],
        lookback_bars=compiled.lookback_bars,
    )


@router.get("/presets")
def presets() -> list[PresetInfo]:
    return [
        PresetInfo(
            name=name,
            label=PRESET_TEXT[name][0],
            description=PRESET_TEXT[name][1],
            execution=ExecutionConfig.preset(name),
            fees=FeeConfig.preset(name),
        )
        for name in ("REALISTIC", "OPTIMISTIC", "CONSERVATIVE")
    ]


@router.get("/collections")
def collections(conn: ConnDep) -> list[CollectionInfo]:
    rows = conn.execute("""
        SELECT c.name, c.created_at, c.definition,
               (SELECT count(*) FROM collection_markets cm WHERE cm.collection_id = c.id),
               (SELECT coalesce(jsonb_object_agg(t.category, t.n), '{}'::jsonb) FROM (
                    SELECT coalesce(m.category, 'unknown') AS category, count(*) AS n
                    FROM collection_markets x JOIN markets m ON m.condition_id = x.condition_id
                    WHERE x.collection_id = c.id GROUP BY 1) t)
        FROM collections c ORDER BY c.created_at
    """).fetchall()
    out = []
    for name, created, definition, n, cats in rows:
        inner: dict[str, Any] = (definition or {}).get("definition") or {}
        sel = inner.get("selection_uses_final_volume")
        note = None
        if sel:
            note = (
                f"Pre-filtered on final lifetime volume >= {sel['min_lifetime_volume']:g}. Only unbiased for "
                f"strategies with min_volume_asof >= {sel['survivorship_free_for_min_volume_asof_at_least']:g}."
            )
        out.append(
            CollectionInfo(
                name=name,
                created_at=created,
                markets=n,
                markets_by_category=dict(sorted(cats.items())),
                definition=inner,
                survivorship_note=note,
            )
        )
    return out


@router.get("/dataset/versions")
def dataset_versions(conn: ConnDep) -> list[DatasetVersionInfo]:
    rows = conn.execute(
        "SELECT id, built_at, content_hash, n_markets, n_bars, n_prints, coverage_start, coverage_end "
        "FROM dataset_versions ORDER BY built_at DESC LIMIT 20"
    ).fetchall()
    return [
        DatasetVersionInfo(
            id=str(r[0]),
            built_at=r[1],
            content_hash=r[2],
            n_markets=r[3],
            n_bars=r[4],
            n_prints=r[5],
            coverage_start=r[6],
            coverage_end=r[7],
        )
        for r in rows
    ]
