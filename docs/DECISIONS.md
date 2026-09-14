# Decisions

## Open product decisions (PRD §20)

The product owner accepted every default on 2026-09-14. Revisit any of these by editing the
row and noting the date.

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| 1 | Venue: international or US exchange? | International | Accepted 2026-09-14 |
| 2 | Warn at $100 bankroll and suggest $1,000? | Yes, non-blocking hint | Accepted 2026-09-14 |
| 3 | Multi-outcome events: one position per event? | One, configurable | Accepted 2026-09-14 |
| 4 | Backtest history retention | Keep all | Accepted 2026-09-14 |
| 5 | Self-hosted vs deployed | Single-user self-hosted | Accepted 2026-09-14 |
| 6 | Legal review before any public distribution | Not distributed; review before that changes | Accepted 2026-09-14 |

## Engineering decisions

### M0

- **Python workspace via uv.** One lockfile (`uv.lock`), one `.venv`, seven packages under
  `apps/`, `packages/`, `services/`, each with a `src/syntha_*` layout. Python pinned to 3.12.
- **Import names are prefixed `syntha_*`** (`syntha_engine`, `syntha_storage`, ...) so nothing
  collides with third-party packages named `engine`, `storage` or `contracts`.
- **The engine no-I/O boundary is a static AST check** (`tools/check_engine_io.py`), run as its
  own CI step and as a pytest test. It forbids network, database, filesystem and subprocess
  modules, `open()`/`__import__`/`eval`/`exec`, dataframe `read_*`/`write_*`/`scan_*`/`sink_*`
  calls, and imports of the I/O-owning sibling packages. If a legitimate need trips it, change
  the rule deliberately in that file, don't work around it.
- **The migration defines the DDL and is written by hand.** The ORM models must match it
  exactly; CI runs the migration against the Timescale image and asserts
  `alembic.autogenerate.compare_metadata` finds no differences.
- **Engine-restricted columns are tagged in the ORM** (`info={"engine_restricted": True}`) so the
  M1/M2 repository layer can strip them mechanically rather than by a hand-maintained list.
- **Health has two endpoints:** `/api/health` (liveness, no dependencies) and
  `/api/health/ready` (checks Postgres and Redis, returns 503 with a body when degraded).
- **The browser only calls its own origin.** Next.js rewrites `/api/*` to FastAPI, so there is
  no CORS configuration and no API URL in client bundles.
- **Contract TS types are hand-mirrored for now** (one type in `system-status.tsx`).
  Codegen from the Pydantic models lands in M3, when the API starts returning real data.
- **No web fonts.** The UI uses system sans and mono stacks, so builds work offline and no third
  party sees requests.

### M1

- **First dataset: seeded stratified sample of 600 markets** scheduled to end in July 2026
  (product owner's choice, 2026-09-14). Over 40,000 markets ended in that month, 88% of them
  tiny sports markets, so ingesting everything was not feasible. Strata are `feeType`
  categories with equal allocation; the sampler sees only market id, condition id and
  `feeType`, never volume, liquidity or outcome. Open and closed markets are both eligible.
  Seed `20260914`. The manifest in `data/raw/_manifests/` is immutable.
- **Raw layer on disk, normalized layer in Postgres.** `data/raw/<endpoint>/<fetch-date>/`
  holds gzipped response envelopes plus an append-only `index.jsonl`. `make rebuild-normalized`
  truncates and reloads the normalized tables in one transaction, then records a
  `dataset_versions` row. Bit-identical means equal content hashes, computed from what Postgres
  returns (`COPY … ORDER BY` primary key), not from what we intended to write.
- **Newest fetch wins** when raw files overlap (same market, same price minute). Trade prints
  are unioned and deduplicated by `print_key`.
- **Staleness comes from trade prints**, because CLOB bars exist for every minute whether or not
  anything traded. `is_stale` = no print in the bar; `staleness_minutes` = minutes since the last
  print (null before the first). Thresholds belong to the backtest config, not the data.
- **Resolution needs both sources to agree, and the chain must confirm.** Gamma's final `outcomePrices` must match the
  on-chain `/v2/resolutions` payout. Otherwise the market is `UNRESOLVED` and the disagreement
  is reported, because a wrong settlement is worse than a missing one. A resolved row whose payout
  we can't parse is also `UNRESOLVED`, never settled on Gamma alone. `resolved_at` comes only
  from `/v2/resolutions`.
- **Markets with missing metadata stay in the dataset** unless the metadata is structurally
  required (tick size, outcome tokens). Unknown `min_order_size` is stored as NULL, and the
  engine must treat such markets as unexecutable rather than dropping them from the universe.
- **Fees are per market plus a dated `fee_type` table**, not the PRD's category table (see
  DATA_NOTES). Ambiguous start dates use the earliest documented date (conservative).
- **Fetching is resumable, not transactional.** Units (a market, a trade walk, a price window)
  are checkpointed in `data/checkpoints/<collection>.jsonl`; failures are logged and retried on
  the next run. The last price window of a still-open market is re-fetched each run.
- **Default rate 5 req/s** (PRD), overridable with `--rps`. Every documented limit is at least
  20 req/s, and Cloudflare throttles rather than rejects.
- **The recorder watches the top 10 markets by 24h volume**, refreshed hourly. Choosing what to
  *record* by current volume is fine: it only decides which future data exists. Backtest
  universes are still selected point-in-time.
- **No surrogate integer token ids yet.** Readability wins until the engine's access patterns
  exist (open item in DATA_NOTES).
