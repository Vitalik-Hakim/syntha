#!/usr/bin/env python3
"""Regenerate the published JSON Schemas from the Pydantic contracts (PRD §8.1).

Usage: python tools/gen_schemas.py [--check]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from syntha_contracts.backtest import BacktestRequest
from syntha_contracts.strategy import StrategyDSL

ROOT = Path(__file__).resolve().parents[1] / "packages" / "contracts"
SCHEMAS = {
    "strategy.schema.json": StrategyDSL,
    "backtest-request.schema.json": BacktestRequest,
}


def render(model: type) -> str:
    return (
        json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )  # type: ignore[attr-defined]


def main(argv: list[str]) -> int:
    stale = []
    for name, model in SCHEMAS.items():
        path = ROOT / name
        text = render(model)
        if "--check" in argv:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
    if stale:
        print(f"out of date: {', '.join(stale)}; run `make schemas`", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
