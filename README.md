# Syntha

A research terminal for backtesting trading strategies against historical Polymarket data,
and being honest about the result.

Paper only. Syntha never places orders, holds funds, touches keys or connects a wallet.

- Spec: [docs/PRD.md](docs/PRD.md)
- Where reality differs from the spec: [docs/DATA_NOTES.md](docs/DATA_NOTES.md)
- Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)

**Status:** M1 (data). Historical ingestion, the raw → normalized rebuild, the data-quality
report and the live order-book recorder exist. No backtest engine or results yet (M2).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`brew install uv`). It installs Python 3.12 for you.
- Node 22+ and [pnpm](https://pnpm.io/) (`brew install pnpm`)
- Docker with Compose v2 (Docker Desktop, OrbStack or Colima)

## Run it

```sh
cp .env.example .env
make dev
```

`make dev` installs dependencies, starts Postgres 16 + TimescaleDB and Redis, applies migrations,
then runs:

| Process | URL |
| --- | --- |
| Web (Next.js) | http://localhost:3100 |
| API (FastAPI) | http://localhost:8100/docs |
| Worker (RQ) | queue `backtests` |

The dashboard's System panel shows whether the API, Postgres and Redis are reachable.

### Troubleshooting

- **`Port 3100 is already in use by ...`**: another dev server has the port. Stop it, or set
  `SYNTHA_WEB_PORT` / `SYNTHA_API_PORT` in `.env`.
- **`no pg_hba.conf entry for host ...`** during `migrate`: the Postgres volume was created by
  a first boot that got interrupted, so it never gained its network auth rule. Run
  `make db-reset`, which **deletes the local database** and re-initialises it.
- **`docker: command not found`** after installing Docker Desktop: open a new terminal so
  `~/.docker/bin` is on your `PATH`.

## Data

Everything fetched is kept as-is under `data/raw/` (gitignored, never edited). The Postgres
tables are rebuilt from it.

```sh
make ingest-sample        # draw the seeded 600-market July 2026 collection (once; immutable)
make ingest-fetch         # fetch metadata, resolutions, trade prints, 1-minute prices (resumable)
make rebuild-normalized   # rebuild Postgres tables from data/raw and record a dataset version
make data-report          # coverage, gaps, staleness, bar-vs-print distance, fee consistency
make verify-normalized    # do the tables still match the latest dataset version's hash?
make m1-acceptance        # rebuild twice (hashes must match) + 500 markets with full history
make recorder             # record the live order book (top 10 markets) — leave it running
```

Other collections: `make ingest-sample COLLECTION=name SAMPLE_START=… SAMPLE_END=… SAMPLE_N=…
SAMPLE_SEED=…`, then `make ingest-fetch COLLECTION=name`. Read
[docs/DATA_NOTES.md](docs/DATA_NOTES.md) before trusting any number: the "price history" is
a sampled mid-like price, not trades, and fees have history the API only partly exposes.

The recorder produces roughly 5–6M order-book delta rows per day for 10 markets. Compression
starts after two days.

## Checks

```sh
make lint              # ruff, mypy, eslint, tsc
make check-engine-io   # engine must not perform I/O (PRD §6)
make test              # pytest
make ci                # everything CI runs, plus the web build
```

Database tests run when `SYNTHA_TEST_DATABASE_URL` is set, which CI does. To run them locally
against the compose database:

```sh
SYNTHA_TEST_DATABASE_URL=postgresql+psycopg://syntha:syntha@localhost:5432/syntha make test
```

Warning: that test downgrades the database to empty and migrates it back up.

## Layout

```
apps/web            Next.js 15 shell (App Router, Tailwind v4)
apps/api            FastAPI                                  syntha_api
packages/contracts  Pydantic models shared by API and engine syntha_contracts
packages/engine     Pure backtest engine, no I/O             syntha_engine
packages/storage    SQLAlchemy models + Alembic migrations   syntha_storage
services/ingestion  Sampling, fetching, raw → normalized      syntha_ingestion  (syntha-ingest CLI)
services/recorder   Live L2 order-book recorder              syntha_recorder
services/worker     RQ backtest workers                      syntha_worker
tools/              Repo tooling (engine I/O boundary check, M1 acceptance)
data/               raw/ layer, checkpoints (gitignored)
```
