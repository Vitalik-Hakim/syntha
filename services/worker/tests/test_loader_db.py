"""DB loader: restricted columns reach only the SettlementTable, only the traded side is loaded,
and a loaded dataset runs end to end. Requires SYNTHA_TEST_DATABASE_URL."""

import dataclasses
import os
from pathlib import Path

import pytest

from syntha_contracts.backtest import BacktestRequest
from syntha_engine.data import MarketTerms
from syntha_engine.runner import run
from syntha_worker.loader import load_dataset
from syntha_worker.testing import seed_minimal_dataset

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "packages" / "storage" / "alembic.ini"


@pytest.fixture
def db() -> str:
    url = os.environ.get("SYNTHA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SYNTHA_TEST_DATABASE_URL not set")
    return seed_minimal_dataset(url)


def _request(**dataset: object) -> BacktestRequest:
    return BacktestRequest.model_validate(
        {
            "strategy": {
                "name": "t",
                "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.5}]},
                "sizing": {"mode": "FIXED_CASH", "value": 5},
            },
            "dataset": {"collection": "c1", **dataset},
            "execution": {
                "model": "LAST_PRICE",
                "slippage_ticks": 0,
                "liquidity_participation": None,
            },
        }
    )


def test_restricted_fields_only_in_settlement_and_only_traded_side_loaded(db: str) -> None:
    loaded = load_dataset(db, _request(start="2026-01-01T00:00:00Z"))
    ds = loaded.dataset
    assert set(ds.markets) == {"M1"}  # collection filter
    assert set(ds.tokens) == {"Y1"}  # BUY_YES loads outcome 0 only
    field_names = {f.name for f in dataclasses.fields(MarketTerms)}
    assert field_names.isdisjoint({"resolution", "resolved_at", "closed_time", "final_volume"})
    s = loaded.settlement.get("M1")
    assert s.resolution == "YES" and s.resolved_ts is not None and s.closed_ts is not None
    tok = ds.tokens["Y1"]
    assert len(tok.bar_ts) == 10 and len(tok.print_ts) == 2
    assert tok.prior_notional == pytest.approx(
        50.0
    )  # the print before `start` counts toward as-of volume
    assert ds.fee_schedules and ds.dataset_version_id == "00000000-0000-0000-0000-0000000000aa"
    assert loaded.content_hash == "hash-1"


def test_loaded_dataset_runs_end_to_end(db: str) -> None:
    loaded = load_dataset(db, _request())
    result = run(_request(), loaded.dataset, loaded.settlement)
    assert result.trades and result.trades[0]["fill_status"] == "FILLED"
    assert result.trades[0]["exit_reason"] == "MARKET_RESOLVED"
