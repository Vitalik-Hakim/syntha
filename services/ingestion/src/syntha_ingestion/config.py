import os
from dataclasses import dataclass, field
from pathlib import Path

from syntha_storage.db import database_url

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"

# CLOB /prices-history rejects windows over 15 days (docs/DATA_NOTES.md). Stay under it.
PRICE_WINDOW_SECONDS = 14 * 86_400
# Data API /v2/resolutions accepts at most 20 condition ids per call.
RESOLUTION_BATCH = 20


@dataclass(frozen=True)
class IngestSettings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("SYNTHA_DATA_DIR", "data")))
    database_url: str = field(default_factory=database_url)
    requests_per_second: float = float(os.environ.get("SYNTHA_INGEST_RPS", "5"))
    concurrency: int = int(os.environ.get("SYNTHA_INGEST_CONCURRENCY", "8"))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def checkpoint_dir(self) -> Path:
        return self.data_dir / "checkpoints"


def psycopg_url(sqlalchemy_url: str) -> str:
    """`postgresql+psycopg://…` → `postgresql://…` for direct psycopg connections (COPY)."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
