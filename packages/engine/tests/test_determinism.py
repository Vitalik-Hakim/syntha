"""Same (config_hash, dataset_version, seed, engine_version) → byte-identical trades and equity
(PRD §17.3), asserted over three fixture configurations including a seeded random strategy."""

from pathlib import Path

import pytest

from syntha_contracts.builtins import builtin_strategy
from syntha_engine.runner import run
from syntha_engine.serialize import canonical_json
from syntha_worker.fixtures import load_fixture

GOLDEN = Path(__file__).parent / "golden"


def _bytes(name: str, random: bool = False) -> tuple[str, str, str]:
    fx = load_fixture(GOLDEN / name)
    request = fx.request
    if random:
        strat = builtin_strategy("random_entry").model_copy(
            update={
                "entry": builtin_strategy("random_entry").entry.model_copy(
                    update={
                        "random": builtin_strategy("random_entry").entry.random.model_copy(  # type: ignore[union-attr]
                            update={"probability_per_bar": 0.3}
                        )
                    }
                )
            }
        )
        request = request.model_copy(update={"strategy": strat})
    r = run(request, fx.dataset, fx.settlement)
    return request.config_hash(), canonical_json(r.trades), canonical_json(r.equity)


@pytest.mark.parametrize(
    ("name", "random"),
    [("single_trade_tp", False), ("compounding", False), ("fee_schedule_change", True)],
)
def test_identical_runs_are_byte_identical(name: str, random: bool) -> None:
    a, b = _bytes(name, random), _bytes(name, random)
    assert a[0] == b[0]
    assert a[1] == b[1]
    assert a[2] == b[2]


def test_seed_changes_random_entries() -> None:
    fx = load_fixture(GOLDEN / "fee_schedule_change")
    base = builtin_strategy("random_entry")
    strat = base.model_copy(
        update={
            "entry": base.entry.model_copy(
                update={"random": base.entry.random.model_copy(update={"probability_per_bar": 0.5})}
            )
        }
    )  # type: ignore[union-attr]
    outs = set()
    for seed in range(6):
        req = fx.request.model_copy(update={"strategy": strat, "seed": seed})
        outs.add(
            canonical_json([t["signal_ts"] for t in run(req, fx.dataset, fx.settlement).trades])
        )
    assert len(outs) > 1, "different seeds should produce different random entries"
