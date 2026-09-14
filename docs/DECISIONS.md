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

### M2

- **Contracts are the source of truth.** `StrategyDSL` and `BacktestRequest` are Pydantic models in
  `syntha_contracts`; `packages/contracts/strategy.schema.json` and `backtest-request.schema.json`
  are generated (`make schemas`), and a test fails if the checked-in schemas drift. Unknown fields
  and unknown feature names are rejected. `BOOK` execution is refused (v2).
- **What the engine receives, split by who may see it.** `MarketTerms` and `TokenData` carry
  pre-trade facts; `SettlementTable` alone carries resolution, resolution time and close time.
  Strategies only ever get a `MarketView` (PRD §5.2). The loader enforces this split at the DB
  boundary.
- **Bar semantics.** Bar k is labelled by its open; its data (last price sample, prints) is complete
  at its close, and time-based features are measured at the close. With the default 1-bar lag a
  signal from bar k executes against bar k+1's prints. Lag 0 is allowed and flagged
  `SAME-BAR FILL — OPTIMISTIC`.
- **Two evaluation paths that must agree.** Entry rules are precomputed per token with NumPy
  (every feature has a causality test). Every *recorded* entry attempt (fills, and the final
  refusal of each signal episode) is re-evaluated through the look-ahead-safe `MarketView` to
  build its rule trace, and the run fails with `EngineInconsistency` if the paths disagree.
  Exits and custom Python strategies always go through `MarketView`.
- **Signal episodes.** A continuously true entry condition is one signal. Failed attempts retry on
  later bars while it holds; the episode ends on a fill, when the condition lapses, or on a
  permanent refusal (unknown minimum size, market closed, end of data). An episode that never
  fills is one trade row with `attempts` and `attempt_refusals`. Found on the first real run:
  without episodes, buy-and-hold produced 1.67M "signals" and trade rows for 118 fills on thin
  markets.
- **Execution pipeline** (PRD §11): reference → adjustment → slippage → tick quantisation →
  bounds → fee. The PRD says both "round_half_even" and "rounded against the trader"; we round
  against the trader (ceiling for buys, floor for sells), which is the conservative reading.
- **TRADE_PRINT** uses all taker prints in the execution bar, both sides: VWAP reference,
  participation cap on printed shares, worst print under adverse selection. No prints means
  `UNEXECUTABLE` unless the fallback is enabled (last price, doubled slippage, flagged).
- **Fees** (per docs/DATA_NOTES.md): `shares × rate × (p(1−p))^exponent`, 5 dp half-even, fee terms
  resolved at each fill's own timestamp (entry and exit can differ across a schedule change). No
  fee at settlement by default (config flag).
- **Sizing.** Notional is clamped in order: `max_notional_per_trade`, cash (fee-inclusive),
  exposure headroom (cap × equity − marked positions), liquidity cap. `size_binding_constraint`
  names the tightest, and the first in that order on ties. Shares are floored to `share_precision`.
  Below the market minimum means `REJECTED_MIN_SIZE`; an unknown minimum means `UNEXECUTABLE`.
- **Exits** trigger on the bar's price and execute through the same model as entries. Partial
  exits are allowed under liquidity caps; unexecutable exits keep the position and re-evaluate on
  the next bar. Exits do not apply the minimum order size (assumption, see DATA_NOTES).
- **Trading halts** from the first bar whose close reaches the market's resolution or close time;
  positions settle at the first bar at or after resolution.
- **End of the backtest.** Without an explicit end date, the clock runs until the last selected
  market resolves, so positions settle rather than being marked. Anything still open at the end is
  valued at its last mark with no costs (`END_OF_BACKTEST`).
- **P&L attribution.** `entry_ref_price`/exit reference = the execution model's reference price
  (VWAP for TRADE_PRINT, bar price for LAST_PRICE). `gross = shares × (exit ref − entry ref)`,
  `slippage = shares × |fill − ref|` on both legs, `net = gross − slippage − fees` (exact in Decimal).
- **Invariants** (PRD §10.2) are asserted after every bar and fail the run: cash ≥ 0, exact
  closed-form cash reconciliation, ledger running balance, position limits, entry within exposure
  headroom.
- **Performance.** Prints are stored sparsely (under 1% of real bars have any), candidate events
  as sorted NumPy arrays, marks computed once per bar. `make bench-engine`: 1,000 markets × 90 days
  of 1-minute bars (129.6M bars), Underdog strategy, **30.0 s, 2.4 GB peak** on a 10-core, 16 GB
  laptop (PRD target: under 60 s). Loading from Postgres is separate: 600 markets / 21M bars take
  about 27 s.
- **CLI.** `strategy-lab run config.json` (PRD M2) writes `request.json`, `manifest.json`,
  `metrics.json`, `data_quality.json`, `trades.csv/jsonl`, `equity.csv` and `ledger.csv` to
  `data/runs/<run id>`. File run ids are `BT-YYYYMMDD-HHMMSS-<config hash>`; the database
  sequence form `BT-YYYYMMDD-NNNNN` arrives with persisted runs in M3.
- **Every run explains its signal count.** `data_quality.entry_funnel` counts bars lost at each
  gate in order (no price yet, trading halted, before open, as-of volume, time to resolution,
  stale price), then how often the entry rules held when eligible and ignoring the universe.
  Found on the real data: the built-in Underdog produced 0 signals on the July sample because none
  of its 58 sports markets ever reached the $10k as-of volume filter (median lifetime notional
  $1.75), although its entry rules held on 115 bars.
- **Not in M2 (per PRD milestones):** bootstrap confidence intervals, null-hypothesis
  comparisons and the preset triple-run (M5); near-miss traces (M4); persisting runs to
  `backtest_runs` (M3).
