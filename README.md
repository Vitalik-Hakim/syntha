# Syntha

A research terminal for backtesting trading strategies against historical Polymarket data,
and being honest about the result.

Paper only. Syntha never places orders, holds funds, touches keys or connects a wallet.

- Spec: [docs/PRD.md](docs/PRD.md)
- Where reality differs from the spec: [docs/DATA_NOTES.md](docs/DATA_NOTES.md)
- Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)

**Status:** M2 (engine). Historical data, the backtest engine and the `strategy-lab` CLI work end
to end. No web results UI yet (M3).

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

## Backtests

```sh
uv run strategy-lab builtins                                   # the four example strategies
uv run strategy-lab validate my-strategy.json                  # schema + feature checks
uv run strategy-lab run examples/underdog-realistic.json       # against the database
uv run strategy-lab run examples/underdog-realistic.json --preset CONSERVATIVE
make m2-acceptance                                             # golden fixtures, look-ahead attacks, determinism
make bench-engine                                              # 1,000 markets × 90 days benchmark
```

**From the browser:** open http://localhost:3100/simulate, pick a strategy, a collection, an
execution preset and starting capital, and submit. The run page streams progress, then shows the
equity curve, cost attribution, data quality and a link to the trade blotter. Runs are queued to
the worker, so `make dev` must be running all three processes. The same API is at
http://localhost:8100/docs (`POST /api/backtests`, `GET /api/backtests/{id}`, `/stream`, `/equity`,
`/trades`, `/export.csv`, `/export.json`).

Every trade in the blotter opens a replay (`/runs/<id>/trades/<seq>`). It shows the price and
trade prints around the decision, the lookback windows the rules read, the exit levels, and
checklists of every filter and rule with the values the engine saw. **Near misses**
(`/runs/<id>/near-misses`) answer "why didn't it trade here?": the bars that came closest to
firing, and the bars where a filter or a portfolio constraint blocked an entry.

**Every result answers "is this better than chance?"** (PRD §15). It shows a bootstrap confidence
interval on ROI and expectancy, a 200-variant permutation test ("your strategy beat N% of
permuted variants"; under ~95% is not evidence of edge), random-entry baselines, and buy-and-hold
on the same markets. "Run all three presets" runs Realistic, Optimistic and Conservative side by
side. `strategy-lab run … --no-honesty` skips the tests for quick iterations.

Two collections so far:

| Collection | What it is | Use it for |
| --- | --- | --- |
| `july-2026-stratified-600` | Seeded, stratified, no outcome-dependent filter | Any strategy; most markets are thin |
| `july-2026-liquid-sports-300` | Sports markets with final volume ≥ $7,500 | Strategies with `min_volume_asof` ≥ $10,000 only; lower filters are survivorship-biased (see DATA_NOTES) |

```sh
uv run strategy-lab run examples/underdog-liquid-sports.json   # the M3 acceptance run, from the CLI
```

A config is a `BacktestRequest` ([schema](packages/contracts/backtest-request.schema.json)); the
strategy inside it is DSL ([schema](packages/contracts/strategy.schema.json)). Each run prints a
summary and writes trades, equity, ledger, metrics and data quality to `data/runs/<run id>/`.
Every summary starts with the PRD's simulated-results disclaimer and any honesty warnings.

## Checks

```sh
make lint              # ruff, mypy, eslint, tsc
make check-engine-io   # engine must not perform I/O (PRD §6)
make test              # pytest
make ci                # everything CI runs, plus the web build
```

Database tests run when `SYNTHA_TEST_DATABASE_URL` is set, which CI does. Run them locally
against a separate `syntha_test` database, **never the dev database**: the tests truncate tables
and downgrade their target to empty.

```sh
docker compose exec postgres psql -U syntha -c "CREATE DATABASE syntha_test"   # once
SYNTHA_TEST_DATABASE_URL=postgresql+psycopg://syntha:syntha@localhost:5432/syntha_test \
SYNTHA_TEST_REDIS_URL=redis://localhost:6379/0 make test
```

## Layout

```
apps/web            Next.js 15 shell (App Router, Tailwind v4)
apps/api            FastAPI                                  syntha_api
packages/contracts  Pydantic models shared by API and engine syntha_contracts
packages/engine     Pure backtest engine, no I/O             syntha_engine
packages/storage    SQLAlchemy models + Alembic migrations   syntha_storage
services/ingestion  Sampling, fetching, raw → normalized      syntha_ingestion  (syntha-ingest CLI)
services/recorder   Live L2 order-book recorder              syntha_recorder
services/worker     DB loader, strategy-lab CLI, RQ workers  syntha_worker     (strategy-lab CLI)
tools/              I/O boundary check, schema generation, M1 acceptance, engine benchmark
examples/           Ready-to-run BacktestRequest configs
data/               raw/ layer, checkpoints, run outputs (gitignored)
```
