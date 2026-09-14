"""Timescale compression for the recorder hypertables.

A 40-second live test on 10 tokens produced about 2,600 delta rows, roughly 5.6M rows/day. Chunks
older than two days are compressed, segmented by token so per-token scans stay fast.
No-op on plain Postgres.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("orderbook_snapshots", "orderbook_deltas")


def upgrade() -> None:
    body = "\n".join(
        f"""    ALTER TABLE {t} SET (timescaledb.compress,
        timescaledb.compress_segmentby = 'clob_token_id', timescaledb.compress_orderby = 'ts');
    PERFORM add_compression_policy('{t}', INTERVAL '2 days', if_not_exists => TRUE);"""
        for t in TABLES
    )
    op.execute(
        f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_name IN ('orderbook_snapshots', 'orderbook_deltas')) THEN
{body}
  END IF;
EXCEPTION WHEN undefined_table THEN
  RAISE WARNING 'timescaledb not installed: recorder compression skipped';
END $$;
"""
    )


def downgrade() -> None:
    body = "\n".join(
        f"""    PERFORM remove_compression_policy('{t}', if_exists => TRUE);
    PERFORM decompress_chunk(c, if_compressed => TRUE) FROM show_chunks('{t}') c;
    ALTER TABLE {t} SET (timescaledb.compress = false);"""
        for t in TABLES
    )
    op.execute(
        f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.hypertables
             WHERE hypertable_name IN ('orderbook_snapshots', 'orderbook_deltas')) THEN
{body}
  END IF;
EXCEPTION WHEN undefined_table THEN
  NULL;
END $$;
"""
    )
