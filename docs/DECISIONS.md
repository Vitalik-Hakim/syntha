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

### M5

- **The honesty layer runs with every backtest** (`syntha_engine.honesty`, stored in
  `backtest_runs.honesty`, migration 0008). Iteration counts are in `BacktestRequest.honesty`,
  so they are part of the config hash. Every random draw is seeded from the request seed with a
  separate stream per test, so reruns reproduce it exactly.
  - **Bootstrap (§15.2):** 10,000 resamples of executed trades' net P&L. Reports ROI 90% CI,
    expectancy 95% CI and the share of resamples losing money. ROI is Σ net P&L / starting
    capital, which ignores path-dependent sizing; the output says so. It is flagged as
    unreliable under 30 trades.
  - **Permutation test (§15.3):** 200 variants. The PRD's "hold entry times fixed, shuffle which
    market each signal maps to (within category)" is implemented on **signal episodes**. Each
    episode keeps its start bar and retry window and moves to a random market of the same
    category that passed the universe filters at that bar. Variants go through the same
    constraints, sizing, execution, fees and exits. The output also reports how many episodes
    had no other eligible market, since a test with few alternatives is weak.
  - **Random-entry baseline (§15.3):** 50 variants with the same number of episodes and retry
    windows, placed uniformly over every eligible (bar, market). This fixes the entry *rate*
    exactly, instead of the built-in's per-bar probability.
  - **Buy and hold (§8.3):** the built-in on the strategy's universe, with the same execution,
    fees and capital, and its own sizing and constraints. It gets its own bootstrap interval,
    because a benchmark is noisy too. Its daily equity is overlaid on the equity chart.
  - **Headline:** "Your strategy beat N% of permuted variants", with "Anything under ~95% is not
    evidence of edge" below 95. Ties count half.
- **Shared preparation, lean simulation.** `runner.run` is now `simulate(prepare(...))`.
  `prepare` builds frames, features, eligibility gates and candidates once. `simulate(prep,
  forced=...)` runs imposed entry candidates without traces, trade rows or equity rows and
  returns a `LeanOutcome`. The test that makes this trustworthy: replaying the strategy's own
  episodes through the lean path reproduces the real run's ROI and fill count exactly, on the
  planted-edge fixture, on the synthetic benchmark and on the real Underdog run.
  A planted-edge test proves the permutation test can find an edge (≥ 95th percentile).
- **Cost.** On `july-2026-liquid-sports-300` each null variant takes about 30 ms: 250 variants plus
  buy-and-hold add about 12 s to a run (CLI total 34 s including a 19 s data load). On the dense
  synthetic benchmark (1,000 markets × 90 days, 77k episodes) a variant takes about 6 s, so the
  defaults would add about 25 minutes. The time is spread across the Python event loop with no
  single hotspot. Lower `honesty.permutations` for exploratory runs of that size; parallelising
  variants across processes is the follow-up.
- **Scenario presets (§11.6).** `POST /api/backtests/presets` creates the request under
  Realistic, Optimistic and Conservative as one group (`backtest_runs.group_id`, `preset`).
  "Run all three presets" is the wizard's primary button, and Optimistic cannot be submitted
  alone. Every grouped result shows the three side by side, Realistic first. A lone Optimistic
  run (possible through the API) shows a banner offering the other two. Every run records its
  preset, or CUSTOM.
- **Wording correction found on real data.** Optimistic is optimistic about *fills*, not
  necessarily about P&L. Underdog lost more under Optimistic (−8.31%) than under Realistic
  (−1.84%), because filling every signal of a losing rule adds more losing trades. The UI no
  longer calls it an upper bound.
- **Also:** an infinite profit factor adds a low-sample warning (§12.3). `ENGINE_VERSION` is
  0.5.0. Runs from before 0.5.0 show "honesty layer not computed; re-run".

### M6

- **Minimum order size: diagnosed before changing anything.** A $100 Favourite Drift run showed
  1,107 REJECTED_MIN_SIZE rows against 20 fills. Classifying each by cause:
  - **66 were sizing:** 5% of equity at a 0.98 price is 4.98 shares, just under the 5-share
    minimum.
  - **1,041 were exposure:** the 25% cap was already used. These were not missed trades. The same
    held conditions kept re-signalling into a full portfolio, inflating "signals generated" from
    26 genuine signals to 1,129. The doomed attempts also held position slots other markets could
    have used.

  Three changes, none of which invents a fill:
  - **Capacity is a constraint, not a signal.** A *new* signal episode is blocked
    (`signals_blocked_by_constraint`: `insufficient_cash` / `exposure_full`) when cash or exposure
    headroom cannot buy the market minimum at the bar's price. Retries of an episode already
    under way are unchanged, which keeps the `cash_constrained` golden fixture intact.
  - **`Sizing.min_order_policy`:** `SKIP` (default, PRD-literal) or `ROUND_UP`. `ROUND_UP` places
    the minimum when max notional, cash (fee-inclusive), exposure headroom and the liquidity cap
    all allow it, and the minimum is at most `round_up_max_multiple` (default 2) times the requested
    notional. Liquidity-capped orders stay rejected: rounding those up would invent liquidity. The
    built-in examples use `ROUND_UP`.
  - **Every rejection records its cause**
    (`sizing.below_min_cause`, `round_up_blocked_by`; `data_quality.signals_rejected_min_size_by_cause`,
    `min_size_rejections_round_up_would_fill`, `entries_rounded_up_to_min_size`). The results page
    names the lever for each cause.

  On that $100 configuration, REJECTED_MIN_SIZE falls from 1,107 to 54 (SKIP) and to 2 (ROUND_UP).
  Because freed slots now go to other markets, results change, so ENGINE_VERSION is 0.6.0.
- **Strategies are versioned documents.** `GET/POST /api/strategies`,
  `POST /api/strategies/{id}/versions`, `GET /api/strategies/{id}/diff?versions=…`,
  `GET /api/strategies/features` (the registry, for the builder). The name identifies the
  strategy. An identical document reuses its version (`created: false`), and versions carry a
  change note (migration 0009). Running a configuration also records its version, so every run
  links to one.
- **Builder** (`/strategies/new`, `/strategies/[id]/edit`): a form over the whole DSL (universe,
  entry with feature and parameter pickers from the registry, sizing including the minimum-order
  policy, ordered exits, constraints) plus a JSON tab. Validation is the API's, debounced as you
  type. "Save and run" opens the simulator on the saved version.
- **Fork, rerun, compare.**
  - `POST /api/backtests/{id}/rerun {overrides, all_presets}` deep-merges overrides into the
    parent's request.
  - The simulator's `?fork=<run>` prefills every setting from a run, and `POST /api/backtests?parent=`
    records `backtest_runs.parent_run_id`.
  - Run pages link "Forked from … · compare with parent".
  - `GET|POST /api/compare` returns the diff of the configurations plus engine and dataset
    versions (which also change results), 23 metrics with server-computed deltas against the
    first run, and LTTB-downsampled equity lines.
  - The runs table selects 2–6 runs to compare.
- **Research reports:** `GET /api/backtests/{id}/report.md` and `GET /api/compare/report.md`,
  self-contained Markdown. They open with the disclaimer and the honesty verdict before any
  headline number, and end with assumptions and reproducibility identifiers.
- **Dev only:** the Next proxy timeout is 15 minutes, so inline-mode runs are not cut off at 30 s.
- **Acceptance evidence.** A browser script:
  1. forked BT-20260914-00011 and changed capital from $1,000 to $2,000;
  2. reran it (BT-20260914-00014);
  3. compared it with its parent (the diff was exactly `starting_capital`; 23 metric rows, equity
     overlay);
  4. downloaded both reports;
  5. built a strategy from an example, saved v1, edited the stop loss and saved v2 (the version
     diff was exactly `exit.any[1].value 0.2 → 0.35`);
  6. compared two runs picked in the runs table.

  No console errors.
- **Strategy research** (docs/DATA_NOTES.md, "M6 collections and strategy research"):
  - Seven candidates were screened on `july-2026-liquid-400` and validated unchanged on the
    `june-2026-liquid-400` holdout, with `tools/research/calibration.py` and
    `tools/research/run_candidates.py`; runs are persisted and browsable.
  - None is robustly profitable. July's best (Momentum Breakout, +5.9% and 97th percentile) lost
    6.7% on June.
  - The two leads worth more data, Overreaction Fade and Favourite to Resolution (YES), are added as
    built-in examples. Their descriptions carry the out-of-sample record, not a recommendation.
  - `execute_run(…, loaded=…)` lets a research batch share one dataset load.

### M7 — strategy search (engineering)

- **`universe.side: EITHER`.** Both tokens of every market are candidates and the entry rules
  choose the side, so one "price ≥ 0.9" rule buys the favourite whichever outcome it is, and a
  "price ≤ 0.1" rule buys the longshot. Each trade row records the side actually bought
  (`BUY_YES` / `BUY_NO`). A market never holds both sides: the complementary token counts as
  `already_holding`. The loader loads both tokens; the permutation test may move an episode to
  the other side of the same market (a legitimate same-category alternative); buy-and-hold on an
  `EITHER` universe buys whichever side is listed first, which makes it a coin-flip-side benchmark
  and is labelled as such. Why: the favourite–longshot and momentum hypotheses are about the
  *price level*, not about YES, and one-sided runs inherit the month's YES base rate
  (docs/DATA_NOTES.md, M6).
- **Null runs skip while holding.** A lean simulation (no equity rows) of a strategy without exit
  rules jumps from event to event (next candidate bar, next settlement) instead of walking every
  bar with open positions. Verified by a lean replay of the real episodes reproducing the real
  ROI on the planted fixture (`test_lean_replay_matches_when_holding_to_resolution`) and on real
  data. Real runs are unchanged: they still mark every bar.
- **Research tooling, outside the app.** `tools/research/candidates.py` is the pre-registered
  grid; `sweep.py` runs it in memory, one process per collection, resumable, writing one JSONL
  line per (configuration, collection) with the trades' P&L and every permutation variant;
  `pooled.py` pools across months, applies the selection rule and computes the best-of-N null
  (PRD §15.4) from the stored variants. Sweeps never write to `backtest_runs`; finalists are
  persisted with `run_candidates.py` so they are browsable. The protocol itself is
  docs/RESEARCH_PROTOCOL.md, written before the discovery run.
- **Data.** `march|april|may-2026-liquid-400`: fresh Gamma listings for each month, same liquid
  pre-filter and stratification as June and July, fetched in parallel at 8 req/s each (Gamma's
  limit is 30/s).
- **Findings** (docs/DATA_NOTES.md, "M7 strategy search"; docs/RESEARCH_PROTOCOL.md): the
  pre-registered rule selected nothing from 40 configurations. The exploratory holdout kept one
  survivor, *Late Longshot to Resolution* (either outcome at 0.10–0.20 within 24 h of the end,
  hold), positive in all five months and +24.4% pooled on the holdout; it is shipped as a built-in
  example labelled **unconfirmed** with its record in the description, and stage 3 of the protocol
  (two untouched months) is pre-registered before it can be called anything more. The Late Fade
  mirror grid was rejected on the holdout. The two M6 leads were rejected. `EITHER` is now the
  default side in the research grid; built-ins that came from single-side research keep their side.
- **Engine limitation found by the anatomy:** `TRADE_PRINT` fills at the next bar's VWAP with no
  reference to the signal price. When a market jumps within the decision lag, a "buy at 0.15"
  signal fills at 0.5–0.99, which a real limit order never would. 16 of the survivor's 146 fills
  were like this (they added +$128). A `max_fill_vs_signal` guard on the execution model is the
  fix; it is a v2 execution change and is listed there rather than patched into M7's results.
- **Resumable market listing, and routing around a poisoned cursor.** `sample` continues a
  window's keyset listing from the newest saved page instead of restarting, and when Cloudflare
  rejects one cursor value with a 403 (August 2026, page 4,612, deterministic; docs/DATA_NOTES.md)
  it re-fetches the previous page one item shorter so the boundary, and the cursor, change. Pages
  are written to the raw store exactly as before; the manifest is only written when the sample
  completes.
- **Stage-3 verdict.** The survivor failed the pre-registered untouched-months test (Feb −29.6%,
  Aug −4.0%). It stays as a built-in example labelled as a failed test, with the full record in
  its description, because a documented negative from a pre-registered search is the product's
  point (PRD §15). Seven months of data are in the normalized layer (3,669 markets, 247 M bars;
  a full rebuild now takes about 41 minutes).

### M8 — PRD v3 engine and instruments (engineering)

- **Marketable-limit orders (E1).** A DSL `order` block (`LIMIT` default, `limit_offset_ticks`
  1, `ttl_bars` 5) applies to entries and exits. The limit is the *signal bar's* price ± k ticks.
  An order is re-quoted every bar of its TTL against the same-side prints at or inside the limit;
  a fill is the eligible VWAP, slipped and quantised against the trader, then capped at the limit
  (`execution.fills._finish`). While it waits the signal stays pending (the episode's attempt is
  pending, so the strategy does not re-signal the same token); when the TTL lapses the row is
  `UNEXECUTABLE_LIMIT_EXPIRED` / `LIMIT_EXPIRED` with the best price seen and bars waited, and the
  episode may re-signal later. `MARKET` keeps the v2 path and every result under it carries
  "MARKET ORDER — NO PRICE PROTECTION". `max_fill_vs_signal` (M7's proposed guard) is subsumed.
- **Same-side liquidity (E2).** `execution.liquidity_side` defaults to `SAME_SIDE`: a buy's
  reference VWAP, participation cap and worst print come from taker-BUY prints, a sell's from
  taker-SELL prints. `BOTH` is the v2 behaviour, kept for comparison and flagged "BOTH-SIDE
  LIQUIDITY — OPTIMISTIC". The golden fixtures pin both: `same_side_cap`, `both_side_liquidity`.
- **Baskets (E3).** `entry.basket` (`NEGRISK_YES_SET` on a `BUY_YES` universe, `NEGRISK_NO_SET`
  on `BUY_NO`; no exit rules — a set is held to settlement) is evaluated per event on an
  `EventView` built point-in-time from the same frames: siblings listed by the bar's close, priced,
  not halted; `catch_all_present` and `neg_risk` from listed siblings only. A vectorised
  pre-screen (Σ open prices vs payoff − min_edge, without fees or ticks) picks the bars worth
  checking, so hold-to-resolution baskets keep the fast bar skip. Execution is all-or-nothing in
  one bar: every leg is quoted under E1/E2; a waitable refusal on any leg rests the whole basket
  within the TTL, any other refusal (or expiry) writes one `UNEXECUTABLE_BASKET_LEG` row and buys
  nothing. Units come from the sizing mode on the basket's cost per unit, then the caps in the
  usual order (max notional, cash, exposure headroom, the scarcest leg's liquidity), then the
  minimum order size with `ROUND_UP` policy. Legs are ordinary positions sharing `basket_id`;
  the SUMMARY row (condition `event:<id>`, token = basket id, side `BASKET`) is synthesised when
  the result is built. Constraints count a basket once (`Portfolio.open_count`), exposure counts
  every leg. Statistics count ordinary trades plus SUMMARY rows and exclude LEG rows
  (`metrics.core.executed_trades`): a basket is one bet. Known limits, on purpose for M8: universe
  filters (`min_volume_asof`, `min_time_to_resolution`) are not applied to baskets, lean null
  runs skip baskets (so the permutation test reports "no signals to permute" for basket
  strategies), and the replay of a SUMMARY row shows its first leg.
- **Print triggers and wallet exits (E4).** `entry.print_trigger` is a per-bar feature: the
  largest qualifying taker print (side, minimum notional, optional wallet allow-list) known at
  the bar's close, executed the next bar like any signal. The wallet id travels on the signal
  and the position; `WALLET_SELL` fires on the first bar in which that wallet prints a taker
  SELL on the same token at or after entry. Wallets are integer ids into `Dataset.wallets`.
- **Statistics (E5).** Cluster bootstrap by `event_id` and by UTC entry day over the same
  resampled-sum machinery as the trade-level bootstrap; clusters are numbered in order of first
  appearance so one trade per cluster reproduces the trade-level interval draw for draw
  (tested). The wider return-per-dollar interval is the headline (`summary.cluster_ci90`,
  `cluster_key`). Return per dollar risked = Σ net / Σ notional (pooled) and the mean of per-trade
  ratios; MDE@80% = 2.48 × the bootstrap SE of return per dollar under the headline clustering.
  Capacity is computed from the traces (`requested_notional`, eligible or bar print volume,
  VWAP) at $1,000 and $10,000, per month of the signal span. The fee counterfactual re-simulates
  the same preparation with the other fee mode (`dataclasses.replace(prep, request=…)`: fees are
  resolved at execution, so nothing else changes); CURRENT decides. `honesty_metrics()` copies
  MDE, the cluster interval and the counterfactual into the stored `metrics` so every run's
  metrics carry them.
- **Register (E6).** `tools/research/register.py` keeps `docs/research/REGISTER.md`; `sweep.py`
  and `run_candidates.py` refuse definitions without a row (`require_registered`); `pooled.py`
  reports best-of-N per family with N from the register, padding unrun registrations with the
  family's own null draws. The 44 M7 configurations were retro-registered with
  `data_seen_at_registration` stating that every month had already been seen, and the notable
  outcomes recorded on their rows; that is an honest record, not a pre-registration.
- **Recorder watchlist v2 (E7).** `recorder_watchlist` (point-in-time rows) is filled by
  `syntha_recorder.watchlist`: 4 categories (fee-type stems, default sports/crypto/politics/
  economics) × {binary, multi (neg-risk)} × {thin: $100–2,000, liquid: ≥ $20,000 of 24h volume},
  3 markets each (48, inside 40–60), from the 1,000 most traded open markets, refilled on every
  refresh; closed markets are retired via `condition_ids` lookups. `--add` watches specific
  condition ids (`manual` | `structural_candidate`) for the S4 scanner. `last_trade_price`
  events are stored as `orderbook_deltas` rows of `event_type = 'trade'` with `fee_rate_bps`.
  `make recorder-coverage` prints token-days, gaps > 5 min, raw bytes/day and the active strata.
  Compression after 1 day (migration 0010). `--top N` remains as the v2 source.
- **Schema (migration 0010).** `trade_prints.proxy_wallet`, `bt_trades.basket_id` /
  `basket_role`, `orderbook_deltas.event_type` / `fee_rate_bps`, `recorder_watchlist`;
  `ENGINE_VERSION` 0.7.0. The reference re-run and its reading are in docs/DATA_NOTES.md ("M8").

### M9 prep — probes, the three decisions and the census (engineering)

- **Three product decisions applied** (2026-09-15; the facts are in docs/DATA_NOTES.md, "M9
  prep"): capacity per month is measured over the collection's coverage window; basket
  strategies are refused (not merely warned) on final-volume-filtered collections, at run
  creation and in `prepare`; a market is a catch-all only after a person reviewed it
  (`catch_all_reviews`, `tools/research/catch_all_review.py`). The M8 text rule and Gamma's
  `negRiskOther` now only nominate candidates for review.
- **Census as a separate ingest mode** (`syntha_ingestion.census`; `make census-sample /
  census-fetch / census-build / census-verify`). Census manifests live in `raw/_census/`, apart
  from collection manifests, so `make rebuild-normalized` never treats a 20,000-market census as a
  collection. The sampler allocates within (category, duration class); category needs the event's
  tags for 2025 markets (no `feeType`), so events are fetched before allocation — the one
  place the census spends requests before the sample exists, and those requests reveal nothing
  outcome-dependent. Observations are pure functions of raw records (tested by hand-derived
  values), the build is deterministic and hashed (`census_runs`), and `payout` is engine-restricted.
- **Depth and truncation.** The print walk stops at `end − 8 d` or 10 pages; `prints_truncated`
  is set only when the cap hit before the depth. A liquidity window or a fill window that the walk
  did not cover yields null, never a partial number. Verified against the busiest July-2025
  market (16 days in 12 pages).
- **CURRENT fees on fee-free months** are the category's latest schedule applied to the fill
  price (fee-free categories, e.g. geopolitics, pay 0), computed with the engine's fee module so
  the census and the backtester agree to the cent.
- **Registrations** (`research/studies/<id>/registration.yaml`, PRD v3 §7.6) are registered by
  `register.py add-registration`: one row for the study and one per primary cell (the BH count),
  plus one per registered rule. S1 registers 660 cells (11 categories × 10 buckets × 6 offsets),
  S2 60 contrast cells and one dynamic rule.
- **Ingestion depends on the engine** (`syntha-engine` added to `syntha-ingestion`) for fee
  arithmetic and `MarketTerms`; the engine still imports nothing from ingestion and performs no
  I/O.

### M9 — census months as engine data, and the discovery tooling (engineering)

- **Census months join the normalized layer as hourly collections.** `make rebuild-normalized`
  now also reads `raw/_census/*.json`: each census month becomes a collection `census-<month>`
  (survivorship-free, with its coverage window) whose markets come from the saved listing pages,
  whose bars are the census's hourly CLOB samples (`price_bars.source = prices_history_60`,
  timestamps on the hour, staleness measured at the bar's last minute) and whose prints are the
  census walks (the last ~8 days of each market). Candidate strategies and the S2 rule run
  through the engine on these collections at `bar_interval_minutes = 60` — the same limit
  orders, same-side liquidity and honesty layer — without a second, 1-minute fetch of every
  month. Limits, on purpose: no bars before `end − 15 d` (lookbacks beyond that are null → no
  signal), prints only in the last 8 days (earlier fills are `NO_PRINTS`, never invented),
  `min_volume_asof` is meaningless on them. A market in both a collection and a census keeps
  its 1-minute collection bars.
- **Sampler cost for 2026 months.** Events are fetched in two stages: before allocation only
  for markets without a `feeType` (category from tags — every 2025 market, few 2026 ones), and
  after allocation for the sampled markets (sibling counts). The CLOB price phase runs on its
  own token bucket (`--prices-rps 40`; documented limit 100/s) while Gamma and the Data API stay
  at 20/s. `tools/research/census_months.sh` runs months back to back with fetch retries and a
  double build per month.
- **`universe.deadline_types`** (BY | ON | NONE, from `markets.deadline_type`) is the S2 rule's
  universe filter; static, applied before anything runs.
- **S1/S2 discovery tool** (`tools/research/s1_calibration.py`): per primary cell (category ×
  fill-price bucket × offset) E[r] under CURRENT fees with the event/day cluster bootstrap
  (wider = headline), fill rate (share of observations whose *mid* is in the bucket that also
  filled), mid calibration, capacity at $1k/$10k, MDE@80%, monthly sign counts; BH at 10% FDR
  with N = 660 from the register (untested cells count as p = 1); the pre-registered selection
  rule; report with heatmaps, negative-space table and S2 test-1 contrasts. The S2 rule's DSL
  (`research/studies/s2_deadline_decay/rule_deadline_decay_no_72h.json`) expresses "YES has not
  decayed" on the traded NO token (`price_change_pct` over 7 days ≤ +10%), because features are
  per token; noted as an approximation of the registered wording.
- **Loader time bounds.** `load_dataset` now bounds its bar and print queries by the selected
  markets' earliest open and latest close (± a day or two) so Timescale excludes chunks: a
  6,147-market census month loaded in 418 s before and 53 s after (the S2 universe of 111 BY
  markets: 73 s → 20 s). On a year-wide 1-minute collection the bounds exclude little and cost a
  few seconds; the data volume dominates there. Per-token LATERAL lookups were tried and are
  worse: each probes every chunk (~0.2 s per token).
- **`bars_waited` on an expired limit order** counts from the bar the order first went live
  (`Signal.first_due_k`); `due_k` moves while the order rests, and the trace used to say 1.
- **Ultra-short markets are classed by the question's window, not only by `end − open`**
  (`parse.question_window_minutes`): 5/15-minute crypto markets are listed days ahead and the
  PRD's rule put them in the primary cells, where they produced a spurious +28% cell
  (docs/DATA_NOTES.md, "M9 discovery, first pass"). Applied at census build time, so a
  manifest's stratum can say `multi_day` while `census_markets.duration_class` says
  `ultra_short`; the allocation caps of already-fetched months are not retroactively changed.
- **`fill_first_10m` next to `fill_10m`.** The registered fill is the window VWAP of taker-BUY
  prints at ≤ mid + 1 tick; it averages over dips in fast markets. The first qualifying print is
  stored as a robustness fill and reported beside it; the selection rule still uses `fill_10m`
  as registered, and any candidate whose two fills disagree is flagged in the report.
- **Normalize streams prints.** With twelve census months in `raw/`, `trade_lines` held
  ~60M print rows as one dict of COPY lines and the rebuild ran out of memory (it exited without
  a word, twice). It now de-duplicates market by market, spools the lines to
  `data/checkpoints/trade_prints.spool`, keeps only per-token timestamp arrays (`array('q')`),
  and orders rows by (market, ts, key) — still deterministic, so the content hash still
  identifies a build. `listing_market_objects` likewise keeps only the sampled market ids
  rather than a month's whole listing.
- **Bulk print load without secondary indexes.** The unique key and the (token, ts) index on
  `trade_prints` are dropped before the COPY and recreated after it (inside the rebuild's
  transaction); row-by-row index maintenance made the 68M-print load take three hours. The
  migration test still sees the same schema.
- **The data-quality report runs in a savepoint with no parallel workers.** The third full
  rebuild (293M bars, 76M prints) loaded everything and then lost the whole transaction: the
  `complement_divergence_prints` self-join ran with parallel workers whose dynamic shared memory
  lives in Docker's 64 MB `/dev/shm`, PostgreSQL raised `DiskFull`, and the `try/except` around
  the report could not save an already-aborted transaction. The report now runs inside
  `conn.transaction()` (a savepoint) with `SET LOCAL max_parallel_workers_per_gather = 0`; a
  failing report is logged and stored as `{"error": ...}` in the version's quality JSON, and
  the load is kept.
- **Disk incident during the fourth full rebuild (2026-09-16).** The rebuild's COPY filled the
  host disk (the Docker VM image had grown to 250 GB): PostgreSQL's data directory remounted
  read-only inside the VM, the quality report failed with `Read-only file system`, and the
  rollback itself could not complete, which left the new relation files (the TRUNCATE'd tables'
  and the new chunks' files, 126 GiB in 1,781 file nodes) orphaned on disk — owned by no row of
  `pg_class`. After a Docker restart PostgreSQL recovered cleanly. Orphans are found by comparing
  `base/<db>/` file nodes with `pg_relation_filenode(oid)` over all of `pg_class` while no other
  backend has a transaction open, and removed by hand; `fstrim` inside the VM returns freed
  blocks to the host. Lesson recorded here rather than automated: a rebuild must start with
  free space of at least the new layer's size (about 120 GB for 14 census months at 60-minute
  bars plus prints) *on top of* the old layer, because the old files live until the commit.
  Truncating the disposable normalized tables before a rebuild halves the peak.
