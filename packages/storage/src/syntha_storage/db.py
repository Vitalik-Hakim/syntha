import os
from typing import Any

from sqlalchemy import Engine, create_engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://syntha:syntha@localhost:5432/syntha"


def database_url() -> str:
    return os.environ.get("SYNTHA_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(
    url: str | None = None, *, connect_timeout_s: int = 5, **kwargs: Any
) -> Engine:
    return create_engine(
        url or database_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout_s},
        **kwargs,
    )
