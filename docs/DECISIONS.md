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

### M3

- **Runs are rows, not files.** `POST /api/backtests` compiles the strategy, pins the latest
  `dataset_versions` row, reuses or creates a `strategy_versions` row (by name + DSL hash) and
  inserts a `QUEUED` `backtest_runs` row. Run ids are `BT-YYYYMMDD-NNNNN` from the
  `backtest_run_seq` sequence (migration 0005). A request against a collection that does not exist
  is 404; an invalid strategy is 422; no dataset version at all is 409.
- **Queue.** RQ on Redis, queue `backtests`, 6 h job timeout. The worker is `SimpleWorker` (no
  fork per job) because forking after NumPy/psycopg imports is unreliable on macOS;
  `SYNTHA_WORKER_FORK=1` restores the forking worker for Linux deployments. `SYNTHA_QUEUE_MODE=inline`
  executes in the request, used by the API tests only.
- **Progress.** The worker publishes `{phase, fraction}` to `syntha:run:<id>:progress` in Redis
  (throttled to 2/s, 1-day TTL). `GET /api/backtests/{id}/stream` is Server-Sent Events polling that
  key; the browser falls back to polling `GET /api/backtests/{id}` if the stream drops. Status is
  always read from Postgres, so a lost Redis key never loses a result.
- **What is persisted.** Every trade row (including refused signal episodes, with rule traces as
  jsonb), the full ledger (`bt_ledger`, new in 0005) and the equity curve **at change points
  only**: a row is kept when cash, positions value, open positions or realized P&L changes, plus
  the first and last. The curve is a step function between kept rows, so nothing is lost.
  Underdog on the liquid sports collection: 302,934 bars → 411 stored rows.
- **Numbers come from the server.** Drawdown is computed over the full stored curve before any
  downsampling; the chart gets at most `max_points` rows via LTTB, always keeping the first and last
  rows, peak and lowest equity, and the max-drawdown trough. Metrics, data quality and the manifest are written once at the end of the
  run. The browser only formats.
- **Refused trades have no price.** An `UNEXECUTABLE` row (no prints, stale, unknown minimum size)
  used to store `entry_price = entry_ref_price = 0`, which the blotter displayed as a real quote.
  Both columns are nullable since migration 0006 and are NULL when no price existed.
  `REJECTED_MIN_SIZE` rows keep their real quote. The `no_liquidity` golden fixture asserts it.
- **Web.** Types are generated from the FastAPI OpenAPI document (`packages/contracts/openapi.json`
  → `apps/web/src/lib/api/schema.d.ts`, `make schemas`), and CI fails on drift in either. The
  browser only calls its own origin; Next rewrites `/api/*` to FastAPI, so SSE and CSV downloads
  need no CORS.
- **`next dev` and `next build` use different output directories** (`.next-dev` and `.next`,
  overridable with `NEXT_DIST_DIR`). Found in M3: a build while the dev server was running replaced
  the dev server's chunks and left every page stuck on "Loading…".
- **Screens (PRD M3).** Simulator wizard (strategy → collection → execution preset → capital), with
  survivorship, lag-0, small-bankroll and lookback warnings before submission. Results page:
  disclaimer, honesty warnings, headline stats (greyed when the sample is small), equity and
  drawdown, cost attribution, exit reasons, why signals didn't fill, data-quality panel with the
  entry funnel, reproducibility manifest. Trade blotter: status filters with counts, cursor
  paging, a drawer with the entry clause checklist and the full sizing/execution trace.
- **Runs report partial execution.** `data_quality.execution_state` is `NO_SIGNALS`, `PARTIAL`
  (at least 25% of signals never filled) or `COMPLETE`, shown as a badge next to the run status,
  so a run whose P&L comes from a minority of its signals doesn't look complete.
- **Survivorship-aware liquid collections.** `ingest-sample --min-lifetime-volume` pre-filters on
  final volume, which is survivorship bias in general (PRD §5.1 rule 3). It is allowed only with
  metadata: the collection records `survivorship_free_for_min_volume_asof_at_least`
  (= `min_lifetime_volume / 0.75`), the API and wizard show the warning, and a run whose
  `min_volume_asof` is below that level gets a `SURVIVORSHIP BIAS` honesty warning. Basis in
  docs/DATA_NOTES.md.
- **Dataset versions stay single-version** (DATA_NOTES "Normalized layer is single-version").
  Each run pins `dataset_version_id` and its hash in the manifest; re-running an old run after a
  rebuild needs the same raw inputs. Versioned snapshots are deferred to M6 (reproducibility).
- **Not in M3:** strategy builder UI and near-miss traces (M4), comparison and robustness (M5),
  cancelling a queued run, deleting runs. Measured on the liquid sports collection, a run takes
  ≈ 41 s end to end: ≈ 19 s loading 2.9M bars from Postgres, ≈ 4 s simulating (2.8 s of it
  formatting 303k equity rows as strings), the rest persisting. Loading is the thing to optimise
  first.

### M4

- **Traces carry every check, with the value the engine saw** (PRD §13.1).
  - Entry traces add `universe`: the universe filters in funnel order (market open, traded
    notional to date, time to scheduled end, staleness), each with op, threshold, actual value and
    pass/fail. They are recomputed per bar from the same data as the vectorised gates. A
    recorded entry that fails one raises `EngineInconsistency`.
  - Clauses with a lookback carry `window` (from/to), the span the feature read: it ends at the
    signal bar's close.
  - Filled entries carry `exit_plan`: take-profit and stop-loss price levels, computed with the
    same arithmetic `evaluate_exit` uses, plus the max-hold deadline.
  - Exit traces carry `checks`, every exit rule evaluated at the bar that fired, with the first
    that held marked `fired`. Each exit fill records the `signal_ts` and `signal_reason` it
    executed: a liquidity-capped exit fills in pieces, each from a re-evaluated signal. Found on
    real data: trade 1 of the Underdog run exited in four fills, and before this change the chart
    showed its only exit signal after three of them.
- **Near misses** (PRD §13.2) are bars where the strategy did not enter, in three kinds:
  - `RULES_FAILED`: eligible bars that came closest to firing. Closeness is failing clauses plus
    their mean relative distance to the threshold (capped at 1 per clause; null counts as 1). Up
    to 3 per market, at least `max(lookback, 60 min)` apart.
  - `BLOCKED_BY_UNIVERSE`: the rules held but a filter blocked the bar. The first bars of up to 2
    evenly spaced stretches per market.
  - `BLOCKED_BY_CONSTRAINT`: the rules and filters held but a portfolio constraint blocked it.
    The first bars of up to 2 stretches per market; `already_holding` is excluded, since that is
    the same signal continuing.

  Per-run caps are 3,000 / 1,000 / 1,000. Every near miss is re-evaluated through the
  look-ahead-safe `MarketView`, and the run fails if that disagrees with why the vectorised path
  skipped the bar. Random and custom Python entries have none. Stored in `bt_near_misses`
  (migration 0007). The score is `numeric` because the schema has no float columns.
- **Exit-reason breakdown** is an engine metric (`metrics.exit_reason_breakdown`). For each reason
  it records trades, share, wins, win rate, net and gross P&L, fees, slippage, mean return and
  median holding time. The results page renders it and links each reason to the filtered blotter.
- **API additions to PRD §14:**
  - `GET /api/backtests/{id}/trades/{seq}/replay` returns raw price samples and prints within ±2 h
    of the trade, plus geometry read from the traces: markers, lookback windows, exit levels,
    resolution time (shown after the fact) and nearby near misses. Prices are downsampled with
    LTTB above 4,000 samples; prints are capped at 20,000.
  - `GET /api/backtests/{id}/near-misses` (filter by kind, blocker or market; order nearest or by
    time) and `/near-misses/{seq}`, both new.
  - `/near-misses/{seq}/replay`, the same replay around a near miss.
- **Screens.**
  - `/runs/[id]/trades/[seq]` is the trade replay. It shows the chart, "why it entered" and "why it
    exited" checklists, the exit plan, sizing and execution tables, exit fills, a timeline and the
    raw traces. ←/→ moves between trades.
  - Blotter rows open it; the M3 drawer is gone.
  - `/runs/[id]/near-misses` and `/runs/[id]/near-misses/[seq]`.
  - The chart scales its y-axis to the prices, prints and markers. Levels far from that range are
    listed as "off chart" instead of flattening the price action.
- **Engine performance.** DSL strategies get no bar window in their `MarketView`; they read only
  features, price and position. Copying the window was the largest cost per view. With that and
  a cheaper exit check, `make bench-engine` (1,000 markets × 90 days, near misses included) runs
  in **32.2 s** (2.26 GB peak), against 30.0 s before M4 and 42.3 s for the first M4 draft.
- **Acceptance evidence.** A browser script opened all 70 blotter rows of the Underdog run on
  `july-2026-liquid-sports-300` (3 FILLED, 31 PARTIAL, 31 UNEXECUTABLE, 5 REJECTED_MIN_SIZE). Each
  replay rendered a chart and a checklist with one row per check in the stored trace, and the
  browser logged no errors.
