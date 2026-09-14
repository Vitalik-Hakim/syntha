"""Run a backtest through the HTTP API end to end (inline queue). Requires SYNTHA_TEST_DATABASE_URL."""

import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from syntha_api.lttb import equity_indices, lttb_indices
from syntha_api.main import create_app
from syntha_api.settings import Settings, get_settings
from syntha_worker.testing import seed_minimal_dataset

REQUEST = {
    "strategy": {
        "name": "api-test",
        "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.5}]},
        "sizing": {"mode": "FIXED_CASH", "value": 5},
    },
    "dataset": {"collection": "c1"},
    "execution": {"model": "LAST_PRICE", "slippage_ticks": 0, "liquidity_participation": None},
}


@pytest.fixture
def client() -> TestClient:
    url = os.environ.get("SYNTHA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SYNTHA_TEST_DATABASE_URL not set")
    seed_minimal_dataset(url)
    app = create_app()
    settings = Settings(
        database_url=url,
        queue_mode="inline",
        redis_url=os.environ.get("SYNTHA_TEST_REDIS_URL", "redis://127.0.0.1:1/0"),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_catalog(client: TestClient) -> None:
    builtins = client.get("/api/strategies/builtins").json()
    assert {b["key"] for b in builtins} >= {"underdog_probability_reversion", "random_entry"}
    presets = client.get("/api/presets").json()
    assert [p["name"] for p in presets] == ["REALISTIC", "OPTIMISTIC", "CONSERVATIVE"]
    cols = client.get("/api/collections").json()
    assert cols[0]["name"] == "c1" and cols[0]["markets"] == 1
    assert client.get("/api/dataset/versions").json()[0]["content_hash"] == "hash-1"


def test_validate_reports_errors_instead_of_raising(client: TestClient) -> None:
    ok = client.post("/api/strategies/validate", json={"dsl": REQUEST["strategy"]}).json()
    assert ok["ok"] and ok["features"] == ["price"]
    bad = client.post(
        "/api/strategies/validate",
        json={
            "dsl": {
                **REQUEST["strategy"],
                "entry": {
                    "all": [
                        {
                            "feature": "price_change_pct",
                            "params": {"lookback_minutes": 7},
                            "op": "gt",
                            "value": 0,
                        }
                    ]
                },
            },
            "bar_interval_minutes": 5,
        },
    ).json()
    assert not bad["ok"] and "multiple" in bad["errors"][0]


def test_run_end_to_end(client: TestClient) -> None:
    created = client.post("/api/backtests", json=REQUEST)
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    assert run_id.startswith("BT-") and created.json()["status"] == "DONE"

    detail = client.get(f"/api/backtests/{run_id}").json()
    assert detail["status"] == "DONE"
    assert detail["metrics"]["n_trades"] == 1
    assert detail["data_quality"]["signals_generated"] == 1
    assert detail["config_hash"] and detail["dataset_version_id"]
    assert detail["preset"] == "CUSTOM" and detail["group"] == []
    assert detail["honesty"]["bootstrap"]["n_trades"] == 1
    assert detail["honesty"]["bootstrap"]["low_sample"] is True

    runs = client.get("/api/backtests").json()["runs"]
    assert runs[0]["id"] == run_id and runs[0]["n_trades"] == 1

    equity = client.get(f"/api/backtests/{run_id}/equity").json()
    assert equity["returned_points"] >= 2
    assert all(0 <= p["drawdown_pct"] <= 1 for p in equity["points"])

    page = client.get(f"/api/backtests/{run_id}/trades").json()
    assert page["total"] == 1 and page["counts_by_status"] == {"FILLED": 1}
    trade = page["trades"][0]
    assert trade["exit_reason"] == "MARKET_RESOLVED" and trade["question"] == "q?"
    full = client.get(f"/api/backtests/{run_id}/trades/{trade['seq']}").json()
    assert full["entry_rule_trace"]["decision"] == "ENTER"

    csv_text = client.get(f"/api/backtests/{run_id}/export.csv").text
    assert csv_text.startswith("seq,fill_status") and "MARKET_RESOLVED" in csv_text
    exported = client.get(f"/api/backtests/{run_id}/export.json").json()
    assert exported["run"]["id"] == run_id and exported["ledger"][0]["type"] == "DEPOSIT"

    with client.stream("GET", f"/api/backtests/{run_id}/stream") as stream:
        body = "".join(stream.iter_text())
    assert "event: progress" in body and "event: status" in body

    # same request twice → a second, separate run (runs are immutable)
    again = client.post("/api/backtests", json=REQUEST).json()
    assert again["run_id"] != run_id


def _run(client: TestClient, strategy: dict[str, object]) -> str:
    created = client.post(
        "/api/backtests", json={**REQUEST, "strategy": {**REQUEST["strategy"], **strategy}}
    )
    assert created.status_code == 201, created.text
    return str(created.json()["run_id"])


def test_trade_replay(client: TestClient) -> None:
    run_id = _run(client, {"exit": {"any": [{"type": "TAKE_PROFIT_PCT", "value": 0.5}]}})
    replay = client.get(f"/api/backtests/{run_id}/trades/1/replay")
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["subject"] == "trade" and body["trade"]["seq"] == 1
    assert body["market"]["question"] == "q?" and body["market"]["resolution"] == "YES"
    kinds = [m["kind"] for m in body["markers"]]
    assert kinds[:2] == ["SIGNAL", "ENTRY"] and "SETTLE" in kinds and "RESOLVED" in kinds
    assert body["markers"] == sorted(body["markers"], key=lambda m: m["ts"])
    # ±2 h around the trade: all ten price samples and all three prints (one is 23:00 the day before)
    assert body["prices"]["total_points"] == 10 and len(body["prints"]["ts"]) == 3
    assert body["prints"]["side"] == ["BUY", "BUY", "SELL"]
    levels = {lv["kind"]: lv["price"] for lv in body["levels"]}
    assert levels["ENTRY_PRICE_RULE"] == 0.5
    assert levels["TAKE_PROFIT_PCT"] == pytest.approx(float(body["trade"]["entry_price"]) * 1.5)
    entry = body["trade"]["entry_rule_trace"]
    assert [c["filter"] for c in entry["universe"]] == ["market_open", "max_stale_minutes"]
    assert body["prev_seq"] is None and body["next_seq"] is None
    assert client.get(f"/api/backtests/{run_id}/trades/9/replay").status_code == 404


def test_near_misses(client: TestClient) -> None:
    run_id = _run(client, {"entry": {"all": [{"feature": "price", "op": "lte", "value": 0.39}]}})
    page = client.get(f"/api/backtests/{run_id}/near-misses").json()
    assert page["total"] >= 1 and page["counts_by_kind"] == {"RULES_FAILED": page["total"]}
    row = page["near_misses"][0]
    assert row["kind"] == "RULES_FAILED" and row["failed_clauses"] == 1
    assert row["failing_features"] == ["price"]
    assert row["score"] == pytest.approx(1 + (0.01 / 0.39) / 2, abs=1e-6)
    detail = client.get(f"/api/backtests/{run_id}/near-misses/{row['seq']}").json()
    clause = detail["trace"]["clauses"][0]
    assert (clause["actual"], clause["passed"]) == (0.4, False)
    replay = client.get(f"/api/backtests/{run_id}/near-misses/{row['seq']}/replay").json()
    assert replay["subject"] == "near_miss" and replay["near_miss"]["seq"] == row["seq"]
    assert replay["markers"][0]["kind"] == "NEAR_MISS" and replay["markers"][0]["ref"] == row["seq"]
    assert [lv["kind"] for lv in replay["levels"]] == ["ENTRY_PRICE_RULE"]

    blocked = _run(client, {"universe": {"min_volume_asof": 1_000_000}})
    page = client.get(
        f"/api/backtests/{blocked}/near-misses", params={"kind": "BLOCKED_BY_UNIVERSE"}
    ).json()
    assert page["total"] == 1
    assert page["near_misses"][0]["blocked_by"] == "below_min_volume_asof"
    assert page["counts_by_blocker"] == {"below_min_volume_asof": 1}
    detail = client.get(f"/api/backtests/{blocked}").json()
    assert detail["data_quality"]["near_misses"]["found"]["BLOCKED_BY_UNIVERSE"] == 1


def test_run_all_three_presets_and_honesty(client: TestClient) -> None:
    body = {
        **REQUEST,
        "honesty": {"bootstrap_iterations": 500, "permutations": 20, "random_baselines": 5},
    }
    created = client.post("/api/backtests/presets", json=body)
    assert created.status_code == 201, created.text
    runs = created.json()["runs"]
    assert [r["preset"] for r in runs] == ["REALISTIC", "OPTIMISTIC", "CONSERVATIVE"]
    assert all(r["status"] == "DONE" for r in runs)

    detail = client.get(f"/api/backtests/{runs[0]['run_id']}").json()
    assert detail["preset"] == "REALISTIC" and detail["group_id"] == created.json()["group_id"]
    assert [g["preset"] for g in detail["group"]] == ["REALISTIC", "OPTIMISTIC", "CONSERVATIVE"]
    assert detail["config"]["execution"]["model"] == "TRADE_PRINT"
    optimistic = client.get(f"/api/backtests/{runs[1]['run_id']}").json()
    assert optimistic["config"]["execution"]["model"] == "LAST_PRICE"

    honesty = detail["honesty"]
    assert set(honesty) >= {
        "bootstrap",
        "permutation",
        "random_baseline",
        "buy_and_hold",
        "summary",
    }
    assert honesty["permutation"]["iterations"] in (0, 20)
    assert honesty["summary"]["headline"]
    assert honesty["buy_and_hold"]["strategy"] == "Buy and Hold to Resolution"
    listed = {r["id"]: r for r in client.get("/api/backtests").json()["runs"]}
    assert listed[runs[2]["run_id"]]["preset"] == "CONSERVATIVE"


QUICK = {"bootstrap_iterations": 200, "permutations": 10, "random_baselines": 5}


def test_strategy_versions_and_diff(client: TestClient) -> None:
    assert len(client.get("/api/strategies/features").json()) == 13
    assert client.get("/api/strategies/builtins").status_code == 200  # not shadowed by /{id}
    saved = client.post("/api/strategies", json={"dsl": REQUEST["strategy"], "note": "first"})
    assert saved.status_code == 201, saved.text
    body = saved.json()
    sid = body["strategy_id"]
    assert (
        body["created"] and body["version"]["version"] == 1 and body["version"]["note"] == "first"
    )

    changed = {
        **REQUEST["strategy"],
        "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.45}]},
    }
    v2 = client.post(
        f"/api/strategies/{sid}/versions", json={"dsl": changed, "note": "tighter"}
    ).json()
    assert v2["created"] and v2["version"]["version"] == 2
    again = client.post(f"/api/strategies/{sid}/versions", json={"dsl": changed}).json()
    assert not again["created"] and again["version"]["version"] == 2

    detail = client.get(f"/api/strategies/{sid}").json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    listed = client.get("/api/strategies").json()
    assert any(s["id"] == sid and s["latest_version"] == 2 for s in listed)
    d = client.get(f"/api/strategies/{sid}/diff", params={"versions": [1, 2]}).json()
    assert d["changes"] == [{"path": "entry.all[0].value", "values": [0.5, 0.45]}]


def test_fork_rerun_compare_and_report(client: TestClient) -> None:
    parent = client.post("/api/backtests", json={**REQUEST, "honesty": QUICK}).json()["run_id"]
    forked = client.post(
        f"/api/backtests/{parent}/rerun",
        json={"overrides": {"starting_capital": "500", "strategy": {"sizing": {"value": 10}}}},
    )
    assert forked.status_code == 201, forked.text
    child = forked.json()["runs"][0]["run_id"]
    detail = client.get(f"/api/backtests/{child}").json()
    assert detail["parent_run_id"] == parent and detail["status"] == "DONE"
    assert detail["config"]["strategy"]["sizing"]["mode"] == "FIXED_CASH"  # merged, not replaced
    bad = client.post(f"/api/backtests/{parent}/rerun", json={"overrides": {"seed": "x"}})
    assert bad.status_code == 422

    cmp = client.get("/api/compare", params={"run_ids": [parent, child]})
    assert cmp.status_code == 200, cmp.text
    comparison = cmp.json()
    paths = {d["path"]: d["values"] for d in comparison["config_diff"]}
    assert paths["starting_capital"] == ["100", "500"] and paths["strategy.sizing.value"] == [
        5.0,
        10.0,
    ]
    assert "run.engine_version" not in paths  # same engine: not a difference
    roi = next(m for m in comparison["metrics"] if m["key"] == "net_profit")
    assert roi["deltas"][0] == 0 and len(roi["values"]) == 2
    assert [e["run_id"] for e in comparison["equity"]] == [parent, child]
    assert client.post("/api/compare", json={"run_ids": [parent, child]}).status_code == 200

    report = client.get(f"/api/backtests/{child}/report.md")
    assert report.status_code == 200 and report.text.count("SIMULATED RESULTS") == 2
    assert "## Is this better than chance?" in report.text and child in report.text
    both = client.get("/api/compare/report.md", params={"run_ids": [parent, child]}).text
    assert "`starting_capital`" in both and "Deltas are against the first run." in both


def test_errors(client: TestClient) -> None:
    assert client.get("/api/backtests/BT-00000000-99999").status_code == 404
    missing = {**REQUEST, "dataset": {"collection": "nope"}}
    assert client.post("/api/backtests", json=missing).status_code == 404
    bad = {
        **REQUEST,
        "bar_interval_minutes": 5,
        "strategy": {
            **REQUEST["strategy"],
            "entry": {
                "all": [
                    {
                        "feature": "price_change_pct",
                        "params": {"lookback_minutes": 7},
                        "op": "gt",
                        "value": 0,
                    }
                ]
            },
        },
    }
    assert client.post("/api/backtests", json=bad).status_code == 422


def test_lttb_keeps_extremes_and_endpoints() -> None:
    x = np.arange(10_000, dtype=float)
    y = np.sin(x / 500)
    y[4321] = 50.0  # a spike must survive downsampling
    keep = lttb_indices(x, y, 200)
    assert keep.size == 200 and keep[0] == 0 and keep[-1] == 9_999 and 4321 in keep
    assert np.all(np.diff(keep) > 0)


def test_equity_downsampling_keeps_the_drawdown_trough() -> None:
    x = np.arange(20_000, dtype=float)
    rng = np.random.default_rng(7)
    equity = 1000 + np.cumsum(rng.normal(0, 1, x.size))
    peak = np.maximum.accumulate(equity)
    drawdown = 1 - equity / peak
    keep = equity_indices(x, equity, drawdown, 100)
    for must in (np.argmax(equity), np.argmin(equity), np.argmax(drawdown), 0, x.size - 1):
        assert must in keep
    assert keep.size <= 103 and np.all(np.diff(keep) > 0)
