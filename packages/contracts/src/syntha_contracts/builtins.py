"""Built-in example strategies (PRD §8.3). All are labelled "not a trading recommendation"."""

from __future__ import annotations

from importlib import resources

from syntha_contracts.strategy import StrategyDSL

BUILTIN_STRATEGY_FILES = {
    "underdog_probability_reversion": "underdog_probability_reversion.json",
    "favourite_drift": "favourite_drift.json",
    "buy_and_hold_to_resolution": "buy_and_hold_to_resolution.json",
    "random_entry": "random_entry.json",
}


def builtin_strategy(key: str) -> StrategyDSL:
    text = (
        resources.files("syntha_contracts.strategies")
        .joinpath(BUILTIN_STRATEGY_FILES[key])
        .read_text()
    )
    return StrategyDSL.model_validate_json(text)


def builtin_strategies() -> dict[str, StrategyDSL]:
    return {key: builtin_strategy(key) for key in BUILTIN_STRATEGY_FILES}
