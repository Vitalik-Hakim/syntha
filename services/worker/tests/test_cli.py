import json
from pathlib import Path

from syntha_worker.cli import main

GOLDEN = Path(__file__).resolve().parents[3] / "packages" / "engine" / "tests" / "golden"


def test_run_against_fixture_writes_outputs(tmp_path: Path, capsys: object) -> None:
    fixture = GOLDEN / "single_trade_tp"
    config = tmp_path / "config.json"
    config.write_text(json.dumps(json.loads((fixture / "fixture.json").read_text())["request"]))
    out = tmp_path / "run"
    assert main(["run", str(config), "--fixture", str(fixture), "--out", str(out)]) == 0
    for name in (
        "manifest.json",
        "metrics.json",
        "data_quality.json",
        "trades.csv",
        "trades.jsonl",
        "equity.csv",
        "ledger.csv",
        "request.json",
    ):
        assert (out / name).exists(), name
    printed = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "SIMULATED RESULTS — NOT ACTUAL TRADING PERFORMANCE" in printed
    assert json.loads((out / "metrics.json").read_text())["n_trades"] == 1


def test_invalid_strategy_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps(
            {
                "strategy": {
                    "name": "x",
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
                    "sizing": {"mode": "FIXED_CASH", "value": 5},
                },
                "bar_interval_minutes": 5,
            }
        )
    )
    assert main(["run", str(config), "--fixture", str(GOLDEN / "single_trade_tp")]) == 2
