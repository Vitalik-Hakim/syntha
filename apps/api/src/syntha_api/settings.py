from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from syntha_storage.db import DEFAULT_DATABASE_URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SYNTHA_", env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL
    redis_url: str = "redis://localhost:6379/0"
    dependency_timeout_s: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
