import io
from collections.abc import Callable
from contextlib import redirect_stdout

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from syntha_storage.models import Base

ConfigFactory = Callable[[str], Config]


def test_migration_renders_offline(alembic_config: ConfigFactory) -> None:
    """Runs without a database: the migration script executes and emits the expected DDL."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        command.upgrade(alembic_config("postgresql+psycopg://offline/offline"), "head", sql=True)
    sql = buf.getvalue()
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} " in sql, table
    assert "create_hypertable('price_bars'" in sql
    assert "create_hypertable('trade_prints'" in sql


def test_migration_matches_models_and_round_trips(
    alembic_config: ConfigFactory, test_database_url: str
) -> None:
    cfg = alembic_config(test_database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(test_database_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            assert diff == [], f"models and migrations disagree: {diff}"

            has_timescale = conn.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            ).scalar()
            if has_timescale:
                hypertables = set(
                    conn.execute(
                        text("SELECT hypertable_name FROM timescaledb_information.hypertables")
                    ).scalars()
                )
                assert {"price_bars", "trade_prints"} <= hypertables
    finally:
        engine.dispose()

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
