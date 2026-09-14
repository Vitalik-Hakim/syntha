# Syntha

A research terminal for backtesting trading strategies against historical Polymarket data,
and being honest about the result.

Paper only. Syntha never places orders, holds funds, touches keys or connects a wallet.

- Spec: [docs/PRD.md](docs/PRD.md)
- Where reality differs from the spec: [docs/DATA_NOTES.md](docs/DATA_NOTES.md)
- Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)

**Status:** M0 (skeleton). No ingestion, engine or results yet.

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
| Web (Next.js) | http://localhost:3000 |
| API (FastAPI) | http://localhost:8000/docs |
| Worker (RQ) | queue `backtests` |

The dashboard's System panel shows whether the API, Postgres and Redis are reachable.

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
services/ingestion  Polymarket API pullers (M1)              syntha_ingestion
services/recorder   Live L2 order-book recorder (M1)         syntha_recorder
services/worker     RQ backtest workers                      syntha_worker
tools/              Repo tooling (engine I/O boundary check)
```
