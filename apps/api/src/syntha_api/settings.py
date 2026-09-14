from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from syntha_storage.db import DEFAULT_DATABASE_URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNTHA_", env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = "redis://localhost:6379/0"
    dependency_timeout_s: int = 2
    # "rq": enqueue for the worker (default). "inline": run inside the request (tests only).
    queue_mode: Literal["rq", "inline"] = "rq"

    @property
    def plain_database_url(self) -> str:
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
