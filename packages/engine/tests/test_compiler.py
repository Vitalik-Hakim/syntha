import pytest

from syntha_contracts.builtins import builtin_strategies
from syntha_contracts.strategy import StrategyDSL
from syntha_engine.strategy.compiler import compile_strategy
from syntha_engine.strategy.features import FeatureError


def test_builtins_compile_at_every_bar_interval_they_support() -> None:
    for s in builtin_strategies().values():
        compile_strategy(s, 1)


def _dsl(**cond: object) -> StrategyDSL:
    return StrategyDSL.model_validate(
        {"name": "t", "entry": {"all": [cond]}, "sizing": {"mode": "FIXED_CASH", "value": 5}}
    )


def test_lookback_must_be_a_multiple_of_the_bar_interval() -> None:
    dsl = _dsl(feature="price_change_pct", params={"lookback_minutes": 10}, op="gt", value=0)
    compile_strategy(dsl, 5)
    with pytest.raises(FeatureError, match="multiple"):
        compile_strategy(dsl, 15)


def test_missing_or_extra_params_are_rejected() -> None:
    with pytest.raises(FeatureError):
        compile_strategy(_dsl(feature="price_change_pct", op="gt", value=0), 1)
    with pytest.raises(FeatureError):
        compile_strategy(_dsl(feature="price", params={"lookback_minutes": 5}, op="gt", value=0), 1)
