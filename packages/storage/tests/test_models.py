from syntha_storage.models import ENGINE_RESTRICTED, Base


def test_every_prd_table_is_mapped() -> None:
    assert set(Base.metadata.tables) == {
        "events",
        "markets",
        "outcomes",
        "price_bars",
        "trade_prints",
        "orderbook_snapshots",
        "fee_schedules",
        "dataset_versions",
        "strategies",
        "strategy_versions",
        "backtest_runs",
        "bt_trades",
        "bt_equity",
    }


def test_engine_restricted_columns_are_tagged() -> None:
    # PRD §5.1 rule 4 / §7: the repository layer strips exactly these before the evaluator.
    restricted = {
        (table.name, col.name)
        for table in Base.metadata.tables.values()
        for col in table.columns
        if col.info.get(ENGINE_RESTRICTED)
    }
    assert restricted == {
        ("markets", "resolved_at"),
        ("markets", "resolution"),
        ("markets", "final_volume"),
    }


def test_no_float_columns() -> None:
    # PRD §7: never floats for cash or prices.
    from sqlalchemy import Float

    floats = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, Float)
    ]
    assert floats == []
