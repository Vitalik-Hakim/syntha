import os
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture
def alembic_config() -> Callable[[str], Config]:
    def make(url: str) -> Config:
        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("sqlalchemy.url", url)
        return cfg

    return make


@pytest.fixture
def test_database_url() -> str:
    url = os.environ.get("SYNTHA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SYNTHA_TEST_DATABASE_URL not set")
    return url
