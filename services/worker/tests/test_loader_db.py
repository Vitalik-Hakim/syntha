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
    # v3 E4: wallets become ids in order of first appearance; NULL stays -1
    assert ds.wallets == ("0xwallet-a",)
    assert tok.print_wallet is not None and tok.print_wallet.tolist() == [0, 0]
    assert ds.markets["M1"].neg_risk is False and ds.markets["M1"].is_catch_all is None
    # M9 decisions: a catch-all is only what a reviewer said it is; capacity months come from
    # the collection's coverage window
    assert ds.coverage == (1767225600, 1769904000)  # 2026-01-01 .. 2026-02-01 UTC


def test_reviewed_catch_all_reaches_the_engine(db: str) -> None:
    request = _request().model_copy(
        update={"dataset": _request().dataset.model_copy(update={"collection": None})}
    )
    loaded = load_dataset(db, request)
    assert loaded.dataset.markets["M2"].is_catch_all is True
    assert loaded.dataset.coverage is None  # no collection, no window


def test_either_side_loads_both_tokens(db: str) -> None:
    request = _request()
    request = request.model_copy(
        update={
            "strategy": request.strategy.model_copy(
                update={"universe": request.strategy.universe.model_copy(update={"side": "EITHER"})}
            )
        }
    )
    loaded = load_dataset(db, request)
    assert set(loaded.dataset.tokens) == {"Y1", "N1"}
    result = run(request, loaded.dataset, loaded.settlement)
    # price <= 0.5 holds for Y1 (0.40) only; the run buys YES, never both sides
    assert [t["side"] for t in result.trades] == ["BUY_YES"]


def test_loaded_dataset_runs_end_to_end(db: str) -> None:
    loaded = load_dataset(db, _request())
    result = run(_request(), loaded.dataset, loaded.settlement)
    assert result.trades and result.trades[0]["fill_status"] == "FILLED"
    assert result.trades[0]["exit_reason"] == "MARKET_RESOLVED"
