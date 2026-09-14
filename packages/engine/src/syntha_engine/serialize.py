"""Canonical serialization of results, so determinism can be asserted byte for byte (PRD §17.3)."""

from __future__ import annotations

import json
from typing import Any

from syntha_engine.runner import BacktestResult


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def result_document(result: BacktestResult) -> dict[str, Any]:
    return {
        "manifest": result.manifest,
        "metrics": result.metrics,
        "data_quality": result.data_quality,
        "trades": result.trades,
        "equity": result.equity,
        "ledger": result.ledger,
    }
