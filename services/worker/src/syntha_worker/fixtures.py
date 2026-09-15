"""Golden-fixture loader (PRD §17.1): small hand-built markets with analytically known answers.

A fixture directory holds:
- `fixture.json`: markets, fee schedules, the BacktestRequest, and the hand-derived expected
  result with its derivation written out;
- `bars.csv`: `clob_token_id,minute,price` (minutes after `start`);
- `prints.csv` (optional): `clob_token_id,minute,second,price,size,side[,wallet]` — `wallet`
  (v3 E4) is any label; ids are assigned in order of first appearance.

Markets may carry `neg_risk` and `is_catch_all` (v3 E3).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from syntha_contracts.backtest import BacktestRequest
from syntha_engine.data import (
    Dataset,
    FeeScheduleEntry,
    MarketTerms,
    Settlement,
    SettlementTable,
    TokenData,
)


@dataclass(frozen=True)
class Fixture:
    name: str
    start: datetime
    request: BacktestRequest
    dataset: Dataset
    settlement: SettlementTable
    expected: dict[str, Any]

    def minute(self, iso: str) -> int:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int((ts - self.start).total_seconds() // 60)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [row for row in csv.DictReader(f) if any(v.strip() for v in row.values())]


def load_fixture(directory: Path) -> Fixture:
    spec = json.loads((directory / "fixture.json").read_text())
    start = datetime.fromisoformat(spec["start"].replace("Z", "+00:00")).astimezone(UTC)
    t0 = int(start.timestamp())

    def at(minute: int | None) -> int | None:
        return None if minute is None else t0 + int(minute) * 60

    markets: dict[str, MarketTerms] = {}
    settlements: dict[str, Settlement] = {}
    token_meta: dict[str, tuple[str, int, str]] = {}
    for m in spec["markets"]:
        cid = m["condition_id"]
        markets[cid] = MarketTerms(
            condition_id=cid,
            event_id=m.get("event_id"),
            category=m.get("category"),
            tick_size=Decimal(m["tick_size"]),
            min_order_size=None
            if m.get("min_order_size") is None
            else Decimal(m["min_order_size"]),
            created_ts=at(m.get("created_minute", 0)),
            open_ts=at(m.get("open_minute", 0)),
            end_ts=at(m.get("end_minute")),
            fees_enabled=m.get("fees_enabled", False),
            fee_type=m.get("fee_type"),
            fee_rate=None if m.get("fee_rate") is None else Decimal(m["fee_rate"]),
            fee_exponent=m.get("fee_exponent"),
            is_catch_all=m.get("is_catch_all"),
            neg_risk=m.get("neg_risk"),
        )
        settlements[cid] = Settlement(
            resolution=m.get("resolution", "UNRESOLVED"),
            resolved_ts=at(m.get("resolved_minute")),
            closed_ts=at(m.get("closed_minute")),
        )
        for t in m["tokens"]:
            token_meta[t["clob_token_id"]] = (
                cid,
                t["outcome_index"],
                t.get("outcome_name", str(t["outcome_index"])),
            )

    bars: dict[str, list[tuple[int, float]]] = {t: [] for t in token_meta}
    for row in _read_csv(directory / "bars.csv"):
        bars[row["clob_token_id"]].append((t0 + int(row["minute"]) * 60, float(row["price"])))
    prints: dict[str, list[tuple[int, float, float, int, int]]] = {t: [] for t in token_meta}
    wallets: dict[str, int] = {}
    for row in _read_csv(directory / "prints.csv"):
        ts = t0 + int(row["minute"]) * 60 + int(row.get("second") or 0)
        side = 1 if row["side"].upper() == "BUY" else -1
        label = (row.get("wallet") or "").strip()
        wallet = wallets.setdefault(label, len(wallets)) if label else -1
        prints[row["clob_token_id"]].append(
            (ts, float(row["price"]), float(row["size"]), side, wallet)
        )

    tokens: dict[str, TokenData] = {}
    for tok, (cid, idx, name) in token_meta.items():
        b = sorted(bars[tok])
        p = sorted(prints[tok])
        tokens[tok] = TokenData(
            clob_token_id=tok,
            condition_id=cid,
            outcome_index=idx,
            outcome_name=name,
            bar_ts=np.array([x[0] for x in b], dtype=np.int64),
            bar_price=np.array([x[1] for x in b], dtype=np.float64),
            print_ts=np.array([x[0] for x in p], dtype=np.int64),
            print_price=np.array([x[1] for x in p], dtype=np.float64),
            print_size=np.array([x[2] for x in p], dtype=np.float64),
            print_side=np.array([x[3] for x in p], dtype=np.int8),
            print_wallet=np.array([x[4] for x in p], dtype=np.int32) if wallets else None,
        )

    schedules = tuple(
        FeeScheduleEntry(
            fee_type=s["fee_type"],
            rate=Decimal(s["rate"]),
            exponent=s["exponent"],
            applies_to=s["applies_to"],
            effective_from=t0 + s["effective_minute"] * 60,
        )
        for s in spec.get("fee_schedules", [])
    )

    request_spec = dict(spec["request"])
    dataset_sel = dict(request_spec.get("dataset", {}))
    dataset_sel.setdefault("start", start.isoformat())
    if spec.get("end_minute") is not None:
        dataset_sel.setdefault("end", (start + timedelta(minutes=spec["end_minute"])).isoformat())
    request_spec["dataset"] = dataset_sel
    request = BacktestRequest.model_validate(request_spec)

    return Fixture(
        name=spec["name"],
        start=start,
        request=request,
        dataset=Dataset(
            markets=markets,
            tokens=tokens,
            fee_schedules=schedules,
            dataset_version_id=f"fixture:{spec['name']}",
            wallets=tuple(wallets),
        ),
        settlement=SettlementTable(settlements),
        expected=spec["expected"],
    )
