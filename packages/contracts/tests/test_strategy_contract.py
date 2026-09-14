import pytest
from pydantic import ValidationError

from gen_schemas import main as check_schemas
from syntha_contracts.backtest import BacktestRequest, ExecutionConfig
from syntha_contracts.builtins import builtin_strategies
from syntha_contracts.strategy import StrategyDSL

MINIMAL = {
    "name": "t",
    "entry": {"all": [{"feature": "price", "op": "lte", "value": 0.2}]},
    "sizing": {"mode": "FIXED_CASH", "value": 5},
}


def test_published_schemas_are_up_to_date() -> None:
    assert check_schemas(["--check"]) == 0, "run `make schemas` and commit the result"


def test_builtin_strategies_are_valid_and_labelled() -> None:
    strategies = builtin_strategies()
    assert set(strategies) == {
        "underdog_probability_reversion",
        "favourite_drift",
        "buy_and_hold_to_resolution",
        "random_entry",
        "overreaction_fade",
        "favourite_to_resolution_yes",
        "late_longshot_to_resolution",
    }
    for s in strategies.values():
        assert s.description and "not a trading recommendation" in s.description


def test_unknown_feature_is_rejected_not_ignored() -> None:
    bad = {**MINIMAL, "entry": {"all": [{"feature": "future_price", "op": "gt", "value": 0}]}}
    with pytest.raises(ValidationError):
        StrategyDSL.model_validate(bad)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        StrategyDSL.model_validate({**MINIMAL, "leverage": 10})


def test_entry_needs_exactly_one_form() -> None:
    with pytest.raises(ValidationError):
        StrategyDSL.model_validate(
            {**MINIMAL, "entry": {"all": [], "random": {"probability_per_bar": 0.1}}}
        )


def test_book_execution_is_refused_in_v1() -> None:
    with pytest.raises(ValidationError, match="BOOK"):
        ExecutionConfig(model="BOOK")  # type: ignore[arg-type]


def test_config_hash_is_stable_and_sensitive() -> None:
    a = BacktestRequest.model_validate({"strategy": MINIMAL})
    b = BacktestRequest.model_validate({"strategy": MINIMAL})
    c = BacktestRequest.model_validate({"strategy": MINIMAL, "seed": 1})
    assert a.config_hash() == b.config_hash() != c.config_hash()


def test_presets_match_prd_table() -> None:
    o, r, c = (ExecutionConfig.preset(p) for p in ("OPTIMISTIC", "REALISTIC", "CONSERVATIVE"))
    assert (o.model, o.slippage_ticks, o.liquidity_participation, o.allow_fill_on_no_prints) == (
        "LAST_PRICE",
        0,
        None,
        True,
    )
    assert (r.model, r.slippage_ticks, r.liquidity_participation, r.partial_fill_policy) == (
        "TRADE_PRINT",
        1,
        0.10,
        "ALLOW",
    )
    assert (
        c.slippage_ticks,
        c.adverse_selection,
        c.liquidity_participation,
        c.partial_fill_policy,
    ) == (2, True, 0.05, "REJECT")
