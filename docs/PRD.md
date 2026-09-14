# Polymarket Strategy Lab — Engineering PRD v2

> Project name: **Syntha**. This document is the product spec as supplied by the
> product owner. It is kept verbatim; deviations discovered during
> implementation are recorded in `docs/DATA_NOTES.md` and `docs/DECISIONS.md`,
> not by editing this file.

**Status:** build-ready spec
**Audience:** Claude Code (autonomous implementation agent) and human reviewers
**Supersedes:** Polymarket Strategy Lab v1 (feature wishlist)

---

## 0. How to use this document

Read sections 1–4 before writing any code. They contain the constraints that
invalidate naive implementations.

**Rules for the implementing agent:**

1. Do not build UI before the engine passes the golden tests in §17.
2. Do not invent data. If a value is not in the dataset, the trade is
   `UNEXECUTABLE` and is recorded as such. There is no interpolation fallback.
3. Every number shown in the UI must be traceable to a row in `trades`,
   `equity_curve`, or `backtest_runs`. No metric may be computed in the browser.
4. When a spec detail here conflicts with what the live Polymarket API actually
   returns, the API wins — record the discrepancy in `docs/DATA_NOTES.md` and
   keep going.
5. Milestones in §18 are sequential and each has hard acceptance criteria.
   Do not start milestone N+1 until N's criteria pass in CI.

---

## 1. Product summary

A web-based research terminal for backtesting trading strategies against
historical Polymarket data.

The product answers exactly one question, honestly:

> "If I had started with $100 and followed this strategy across these historical
> Polymarket markets, using only information available at the time and only
> trades that could actually have filled, what would my portfolio be worth?"

The product is paper-only. It never places orders, never custodies funds, never
touches a private key, and never connects a wallet.

### 1.1 The three things that make this hard

Everything else is CRUD. These three are the product:

| Hard problem | Why it kills naive backtesters |
| --- | --- |
| **Look-ahead bias** | Filtering the market universe by *final* volume, or reading the resolution outcome, silently turns a losing strategy into a winner. |
| **Execution realism** | Prediction market books are thin. Assuming you filled 500 shares at the chart price is a fantasy at 8¢. |
| **Statistical honesty** | 83 trades with a 68% win rate is indistinguishable from noise. The product must say so. |

If the engine gets these right and the UI is mediocre, the product is valuable.
If the UI is beautiful and these are wrong, the product is worse than useless —
it manufactures false confidence about real money.

---

## 2. Scope

### 2.1 In scope (v1)

- Historical ingestion of Polymarket market metadata, price history, and public
  trade prints.
- Point-in-time-correct universe selection.
- A versioned, serializable strategy DSL plus a visual builder that emits it.
- Deterministic event-driven backtest engine with portfolio accounting.
- Two execution models: `LAST_PRICE` and `TRADE_PRINT` (see §11).
- Configurable slippage and a time-aware Polymarket fee model.
- Results: equity curve, trade blotter, per-trade replay with rule-by-rule
  explanation, drawdown, distribution stats, and honest significance testing.
- CSV/JSON export and a reproducible run manifest.

### 2.2 Explicitly out of scope (v1)

- Order-book (L2) execution simulation. **Reason in §4.2.** Deferred to v2 once
  the recorder has accumulated data.
- Parameter optimization sweeps. Deferred to v2 *after* out-of-sample splits
  exist, because optimization without OOS is a foot-gun.
- Live trading, paper trading against live feeds, alerts, scanners.
- Multi-user accounts, billing, sharing. Single-user local/self-hosted first.
- Any market-making, maker-rebate, or resting-limit-order strategy. v1 models
  the user as a **taker only**. Modelling a resting limit order requires knowing
  whether someone would have hit it, which the data cannot support.

### 2.3 Non-goals as product behaviour

The app must never:

- Display a projected or extrapolated future return.
- Show a "profit celebration" animation, confetti, or a leaderboard.
- Round a negative result toward zero or hide fee/slippage lines.
- Present a backtest with <30 trades without a prominent low-sample warning.

---

## 3. Domain model: Polymarket mechanics the engine must encode

Get these wrong and every number is wrong.

### 3.1 Shares, not bets

A market resolves to a set of outcome tokens. Each token settles at **$1.00** if
its outcome occurs and **$0.00** otherwise. Tokens trade between $0.01 and
$0.99. The price *is* the market-implied probability, so **price and probability
are the same field** — v1 of this PRD treated them as separate strategy inputs,
which duplicates conditions. Store one field, `price`, and let the UI label it
as probability where that reads better.

Position value = `shares × price`. P&L on exit = `shares × (exit_price −
entry_price)` minus costs. P&L on resolution = `shares × (1 or 0 − entry_price)`
minus entry costs.

### 3.2 Identifiers

| Concept | Identifier | Source |
| --- | --- | --- |
| Event (e.g. a match) | `event_id`, `slug` | Gamma API |
| Market (a question) | `condition_id` | Gamma API |
| Outcome token | `clob_token_id` (ERC-1155 asset id) | Gamma `clobTokenIds` |

**Price history is keyed by `clob_token_id`, not by market.** Trade prints are
keyed by `condition_id`. The ingestion layer must join these; a large share of
early bugs will be identifier confusion. Store all three on every row.

### 3.3 Binary complement

For a standard binary market, `price(YES) + price(NO) ≈ 1`. Two consequences:

- **There is no shorting.** A strategy that wants to be short YES buys NO. The
  engine must expose this as a `side: BUY_YES | BUY_NO` decision, never as a
  negative position size.
- Complementary prices are not exactly complementary in practice (spread,
  stale prints). Never synthesise the NO series as `1 − YES`; ingest both token
  series independently and record the divergence as a data-quality metric.

### 3.4 Multi-outcome and neg-risk markets

Events with more than two outcomes are modelled as several linked binary
markets whose YES prices sum to roughly 1. The engine treats each as an
independent tradable token but must expose `event_id` so that:

- exposure limits can optionally be applied per event, not just per market;
- a strategy does not accidentally hold every outcome of the same event, which
  is a synthetic cash position, not a bet.

### 3.5 Resolution

- A market has `end_date` (scheduled) and `resolved_at` (actual). These differ,
  sometimes by days, because of the resolution/dispute process.
- Positions open at `resolved_at` settle at 1 or 0. Exit reason =
  `MARKET_RESOLVED`.
- Markets can resolve **invalid / 50-50**. The `resolution` field must be an
  enum `{YES, NO, INVALID, UNRESOLVED, OUTCOME_<n>}` and the settlement path for
  `INVALID` (typically $0.50) must be explicit, not a crash.
- A market can also stop trading before resolution. If the price series ends
  more than `max_stale_minutes` before `resolved_at`, the position is marked
  `SETTLED_ON_STALE_DATA` and flagged in the data-quality panel.

### 3.6 Order constraints

Read these off the market object at ingestion; do not hardcode:

- `minimum_tick_size` — fills must be quantised to this.
- `minimum_order_size` — orders below it are `REJECTED_MIN_SIZE`, not silently
  rounded up.
- Resting (maker) orders carry a higher share minimum than immediate-or-cancel
  (taker) orders. Since v1 is taker-only, use the per-market minimum, but store
  both.
- Shares are fractional but should be rounded to a configured precision
  (default 2 dp) with the residual returned to cash.

### 3.7 Fees — and why they are time-dependent

Polymarket charges **takers only**; makers pay zero. The fee is not flat, it is
a function of price:

```
fee_usd = C × θ × p × (1 − p)
```

where `C` = number of shares, `p` = fill price in dollars, and `θ` is a
category-specific rate. The fee peaks at p = 0.50 and falls to near zero at the
extremes. Rounding is to the nearest cent, banker's rounding (half-to-even).

Approximate international θ values as of mid-2026 — **verify against
`https://docs.polymarket.com` and `help.polymarket.com` at build time and store
the schedule as data, not code:**

| Category | θ | Max fee / 100 shares |
| --- | --- | --- |
| Geopolitics, world events | 0.00 | $0.00 |
| Politics, finance, tech, mentions | 0.04 | $1.00 |
| Sports, economics, culture, weather, other | 0.05 | $1.25 |
| Crypto | 0.07 | $1.75 |

The US-regulated exchange uses a uniform θ ≈ 0.06 with a maker rebate.

**Critical for backtesting:** Polymarket was fee-free for most of its history
and rolled fees out progressively during 2026 (crypto first, then sports, then a
broad schedule, with rates revised again mid-year). A backtest of 2024 data that
applies 2026 fees is wrong, and a backtest of 2026 data that applies zero fees
is wrong in the other direction.

Therefore the fee model is a **table of dated schedules**:

```sql
fee_schedules(
  id, effective_from timestamptz, effective_to timestamptz,
  category text, theta numeric, venue text, source_url text, notes text
)
```

and the engine resolves θ by `(category, fill_timestamp, venue)`. The user may
override with one of three modes, shown explicitly in results:

- `HISTORICAL` (default) — the schedule in force at each fill.
- `CURRENT` — today's schedule applied throughout (answers "would this work
  now?").
- `FIXED` — a user-supplied flat rate or zero, labelled as counterfactual.

Also model the market's own `fees_enabled` flag where the API exposes it.

---

## 4. Data reality check

This section replaces §39–40 of v1, which assumed data that is not
retroactively available.

### 4.1 What is actually obtainable

| Data | Endpoint | Notes |
| --- | --- | --- |
| Market + event metadata | `GET https://gamma-api.polymarket.com/markets`, `/events` | Public, no auth. Gives `conditionId`, `slug`, `outcomes`, `clobTokenIds`, `volume`, `liquidity`, `endDate`, category/tags. |
| Price history per token | `GET https://clob.polymarket.com/prices-history` | Params: `market` (= clob token id, **required**), `startTs`, `endTs`, `interval` ∈ {max, all, 1m, 1w, 1d, 6h, 1h}, `fidelity` (minutes, default 1). Returns `{history: [{t, p}]}`. |
| Batch price history | `POST https://clob.polymarket.com/batch-prices-history` | Body: `{markets: [...], start_ts, end_ts, fidelity}`. Use for bulk backfill. |
| Historical trade prints | `GET https://data-api.polymarket.com/trades` | Keyed by condition id; gives side, size, price, timestamp. **This is the single most valuable dataset in the product** — see §4.3. |
| Current order book | `GET https://clob.polymarket.com/book` | **Live only.** No history. |
| Live book stream | `wss://ws-subscriptions-clob.polymarket.com/ws/` | Channel `market`. Use for the recorder in §4.4. |

Error responses include `retry_after_seconds`; honour it.

### 4.2 What is NOT obtainable — and what it means

**There is no historical order-book API.** You cannot retrieve the bid/ask depth
that existed at 14:32 on 12 June for a market that has already resolved. v1's
"Model B — Bid/Ask" and "Model C — Order Book" execution models cannot be
implemented against historical data, and any implementation claiming to do so is
fabricating the book.

Consequences, all mandatory:

- v1 ships two execution models only: `LAST_PRICE` and `TRADE_PRINT`.
- The execution-model selector shows book-based models as disabled with the
  tooltip: *"Requires recorded L2 data. Available only for markets captured by
  the live recorder from <date> onward."*
- The `orderbook_snapshots` table exists from day one, populated **only** by the
  recorder (§4.4), and the engine reads it when present.

### 4.3 Trade prints are the honest execution proxy

`prices-history` returns a smoothed/aggregated series. Executing against it is
the optimistic case. Public trade prints give actual (price, size, timestamp,
side) tuples that really happened. From these you can derive, per bar:

- `traded_volume`, `vwap`, `high`, `low`, `n_prints`, `print_side_imbalance`;
- a **liquidity plausibility check**: if the strategy wants 200 shares in a bar
  where total prints were 40 shares, the fill is implausible.

Define the `TRADE_PRINT` model in §11.3 around exactly this.

### 4.4 The recorder (build it in M1, it compounds)

A small always-on service that subscribes to the CLOB websocket for a
configurable watchlist and writes L2 snapshots and deltas to
`orderbook_snapshots`. It produces nothing useful on day one and produces the
only true bid/ask backtesting dataset the product will ever have by month three.
Start it early precisely because the data cannot be backfilled.

### 4.5 Dataset versioning and immutability

Two layers:

- `raw/` — append-only, exactly what the API returned, gzipped JSON/parquet,
  partitioned by `endpoint/date/`, with the request URL and fetch timestamp.
  Never edited.
- `normalized/` — derived tables (§7). Fully rebuildable from `raw/` by a single
  command: `make rebuild-normalized`.

Every normalization run produces a `dataset_versions` row with a content hash.
Backtests reference `dataset_version_id`. Re-running a backtest against a
different dataset version produces a **new** run; results are never mutated
in place.

### 4.6 Ingestion job requirements

- Token-bucket rate limiter, conservative default (start at 5 req/s), exponential
  backoff with jitter, honour `retry_after_seconds`, resume from checkpoint.
- Chunked backfill: request narrow `startTs`/`endTs` windows with `fidelity=1`
  rather than one wide `interval=max` call, otherwise the API returns coarse
  aggregation and you silently lose granularity.
- Idempotent upserts on `(clob_token_id, ts)`.
- Every job logs rows-in, rows-written, rows-rejected, and gaps detected.

---

## 5. Point-in-time correctness (the invariant that matters most)

### 5.1 The rules

1. **Bar visibility.** When evaluating at bar `t`, the strategy may read bars
   with timestamp ≤ `t`. Access to `t+1` or later must be structurally
   impossible, not merely avoided by convention (see §5.2).
2. **Decision lag.** A signal generated from bar `t` executes against bar
   `t + decision_lag_bars`. Default `1`. Setting it to `0` is allowed but the
   results header must render `SAME-BAR FILL — OPTIMISTIC`.
3. **Point-in-time universe.** A market enters the universe at
   `market_open_ts`, not at ingestion time. Filters on volume and liquidity must
   use the value **as of the evaluation timestamp**, reconstructed from
   cumulative prints — never the final lifetime `volume` field from Gamma.
   Filtering on final volume is survivorship bias and is the single most common
   way to fake a profitable prediction-market backtest.
4. **No resolution leakage.** `resolution`, `resolved_at`, and any field derived
   from them are unavailable to the strategy evaluator. They are visible only to
   the settlement routine.
5. **No backfill.** Forward-filling a stale price is permitted (it uses only the
   past) but the bar must carry `is_stale=true` and `staleness_minutes`.
   Interpolating between a past and a future observation is forbidden.
6. **Delisted and cancelled markets stay in the universe.** Excluding markets
   that were removed or resolved invalid is survivorship bias.

### 5.2 Enforce it in the type system, not in review

The strategy evaluator receives a `MarketView` object, not the dataframe. The
view is constructed per bar and exposes only:

```python
class MarketView:
    now: datetime
    price: float | None
    bars: BarWindow          # indexable only with negative offsets: bars[-1], bars[-10]
    features: Mapping[str, float | None]
    market: MarketStatic     # id, category, tick_size, min_size, open_ts ONLY
    position: Position | None
```

`BarWindow.__getitem__` raises on any non-negative index. `MarketStatic` is a
frozen dataclass that physically does not contain `resolution`, `resolved_at`,
`final_volume`, or `end_date_actual`.

Add a CI test (§17.4) that constructs a "clairvoyant" strategy attempting each
form of leakage and asserts it raises.

---

## 6. System architecture

```
apps/
  web/                     Next.js 15 (App Router), TypeScript, Tailwind
  api/                     FastAPI
services/
  ingestion/               Gamma + CLOB + Data API pullers, rate limiting
  recorder/                Live WS L2 recorder (M1)
  worker/                  RQ workers for backtests
packages/
  engine/                  Pure Python. NO network, NO database imports.
    strategy/              DSL schema, parser, compiled evaluator, features
    execution/             Fill models, slippage, fees
    portfolio/             Cash ledger, positions, exposure, settlement
    metrics/               Returns, drawdown, distribution, significance
    runner.py              The event loop
  storage/                 SQLAlchemy models, repositories, migrations
  contracts/               Pydantic models shared by api + engine; TS types generated
```

**Hard boundary:** `packages/engine` has no I/O. It takes a
`BacktestRequest` plus an in-memory `Dataset` and returns a `BacktestResult`.
This is what makes the golden tests in §17 possible. Any `import requests` or
`import sqlalchemy` inside `engine/` fails CI.

Stack: Python 3.12, FastAPI, Polars, NumPy, Pydantic v2, PostgreSQL 16 (+
TimescaleDB hypertables on the two time-series tables), Redis + RQ, Next.js +
TypeScript + Tailwind + TanStack Table + uPlot or lightweight-charts for the
time series (both handle 100k+ points; Recharts does not).

---

## 7. Data model

Timestamps are `timestamptz`, always UTC. Money is `numeric(18,6)`. Prices are
`numeric(8,6)`. Never use floats for cash.

```sql
CREATE TABLE events (
  id                text PRIMARY KEY,
  slug              text NOT NULL,
  title             text NOT NULL,
  category          text,
  tags              text[],
  created_at        timestamptz,
  ingested_at       timestamptz NOT NULL
);

CREATE TABLE markets (
  condition_id      text PRIMARY KEY,
  event_id          text REFERENCES events(id),
  slug              text NOT NULL,
  question          text NOT NULL,
  category          text,
  neg_risk          boolean NOT NULL DEFAULT false,
  tick_size         numeric(8,6) NOT NULL,
  min_order_size    numeric(18,6) NOT NULL,
  fees_enabled      boolean,
  open_ts           timestamptz,          -- first tradable moment
  end_date          timestamptz,          -- scheduled
  resolved_at       timestamptz,          -- actual; ENGINE-RESTRICTED
  resolution        text,                 -- ENGINE-RESTRICTED
  final_volume      numeric(18,2),        -- ENGINE-RESTRICTED
  ingested_at       timestamptz NOT NULL
);

CREATE TABLE outcomes (
  clob_token_id     text PRIMARY KEY,
  condition_id      text NOT NULL REFERENCES markets(condition_id),
  outcome_index     int  NOT NULL,
  outcome_name      text NOT NULL,
  UNIQUE (condition_id, outcome_index)
);

-- Timescale hypertable, chunk 7 days
CREATE TABLE price_bars (
  clob_token_id     text NOT NULL,
  ts                timestamptz NOT NULL,
  price             numeric(8,6) NOT NULL,
  is_stale          boolean NOT NULL DEFAULT false,
  staleness_minutes int,
  source            text NOT NULL,        -- 'prices_history' | 'derived_prints'
  PRIMARY KEY (clob_token_id, ts)
);

-- Timescale hypertable, chunk 7 days
CREATE TABLE trade_prints (
  id                bigserial PRIMARY KEY,
  condition_id      text NOT NULL,
  clob_token_id     text NOT NULL,
  ts                timestamptz NOT NULL,
  price             numeric(8,6) NOT NULL,
  size              numeric(18,6) NOT NULL,
  side              text NOT NULL         -- taker side: BUY | SELL
);
CREATE INDEX ON trade_prints (clob_token_id, ts);

-- Populated ONLY by the live recorder. Empty for historical periods.
CREATE TABLE orderbook_snapshots (
  clob_token_id     text NOT NULL,
  ts                timestamptz NOT NULL,
  bids              jsonb NOT NULL,       -- [[price, size], ...] descending
  asks              jsonb NOT NULL,       -- ascending
  PRIMARY KEY (clob_token_id, ts)
);

CREATE TABLE fee_schedules (
  id                bigserial PRIMARY KEY,
  venue             text NOT NULL,        -- 'international' | 'us'
  category          text NOT NULL,
  theta             numeric(8,6) NOT NULL,
  effective_from    timestamptz NOT NULL,
  effective_to      timestamptz,
  source_url        text NOT NULL
);

CREATE TABLE dataset_versions (
  id                uuid PRIMARY KEY,
  built_at          timestamptz NOT NULL,
  content_hash      text NOT NULL,
  n_markets         int, n_tokens int, n_bars bigint, n_prints bigint,
  coverage_start    timestamptz, coverage_end timestamptz,
  notes             text
);

CREATE TABLE strategies (
  id                uuid PRIMARY KEY,
  name              text NOT NULL,
  created_at        timestamptz NOT NULL
);

CREATE TABLE strategy_versions (
  id                uuid PRIMARY KEY,
  strategy_id       uuid NOT NULL REFERENCES strategies(id),
  version           int  NOT NULL,
  dsl               jsonb NOT NULL,
  dsl_hash          text NOT NULL,
  created_at        timestamptz NOT NULL,
  UNIQUE (strategy_id, version)
);

CREATE TABLE backtest_runs (
  id                text PRIMARY KEY,     -- BT-YYYYMMDD-NNNNN
  strategy_version_id uuid NOT NULL REFERENCES strategy_versions(id),
  dataset_version_id  uuid NOT NULL REFERENCES dataset_versions(id),
  config            jsonb NOT NULL,       -- full BacktestRequest
  config_hash       text NOT NULL,
  engine_version    text NOT NULL,
  seed              bigint NOT NULL,
  status            text NOT NULL,        -- QUEUED|RUNNING|DONE|FAILED
  started_at        timestamptz, finished_at timestamptz,
  error             text,
  metrics           jsonb,                -- denormalized summary
  data_quality      jsonb NOT NULL        -- §16
);

CREATE TABLE bt_trades (
  id                bigserial PRIMARY KEY,
  run_id            text NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  seq               int NOT NULL,
  condition_id      text NOT NULL,
  clob_token_id     text NOT NULL,
  side              text NOT NULL,        -- BUY_YES | BUY_NO
  signal_ts         timestamptz NOT NULL,
  entry_ts          timestamptz NOT NULL,
  entry_price       numeric(8,6) NOT NULL,   -- realized, incl. slippage
  entry_ref_price   numeric(8,6) NOT NULL,   -- unadjusted bar price
  shares            numeric(18,6) NOT NULL,
  notional          numeric(18,6) NOT NULL,
  entry_fee         numeric(18,6) NOT NULL,
  exit_ts           timestamptz,
  exit_price        numeric(8,6),
  exit_fee          numeric(18,6),
  exit_reason       text,                 -- §12.4 enum
  gross_pnl         numeric(18,6),
  fees_total        numeric(18,6),
  slippage_cost     numeric(18,6),
  net_pnl           numeric(18,6),
  return_pct        numeric(12,6),
  holding_seconds   int,
  entry_rule_trace  jsonb NOT NULL,       -- §13.4
  exit_rule_trace   jsonb,
  fill_status       text NOT NULL         -- FILLED|PARTIAL|UNEXECUTABLE|REJECTED_MIN_SIZE
);
CREATE INDEX ON bt_trades (run_id, entry_ts);

CREATE TABLE bt_equity (
  run_id            text NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  ts                timestamptz NOT NULL,
  cash              numeric(18,6) NOT NULL,
  positions_value   numeric(18,6) NOT NULL,
  equity            numeric(18,6) NOT NULL,
  open_positions    int NOT NULL,
  realized_pnl      numeric(18,6) NOT NULL,
  unrealized_pnl    numeric(18,6) NOT NULL,
  exposure_pct      numeric(8,4) NOT NULL,
  PRIMARY KEY (run_id, ts)
);
```

Columns marked `ENGINE-RESTRICTED` are stripped by the repository layer before
the `Dataset` object reaches the engine's strategy evaluator. They are passed
separately, in a `SettlementTable`, which only the settlement routine receives.

---

## 8. Strategy DSL

The visual builder is a *view*. The DSL is the artifact: versioned, hashable,
diffable, exportable.

### 8.1 Shape

```jsonc
{
  "dsl_version": 1,
  "name": "Underdog Probability Reversion",
  "universe": {
    "side": "BUY_YES",
    "categories": ["Sports"],
    "min_volume_asof": 10000,
    "min_time_to_resolution_minutes": 30
  },
  "entry": {
    "all": [
      { "feature": "price",              "op": "lte", "value": 0.20 },
      { "feature": "price_change_pct",   "params": {"lookback_minutes": 10},
        "op": "gte", "value": 0.03 },
      { "feature": "traded_volume",      "params": {"lookback_minutes": 10},
        "op": "gte", "value": 50 }
    ]
  },
  "sizing": {
    "mode": "PCT_OF_EQUITY",         // FIXED_CASH | PCT_OF_EQUITY | PCT_OF_INITIAL | FIXED_SHARES
    "value": 0.05,
    "max_notional_per_trade": 25.0
  },
  "exit": {
    "any": [
      { "type": "TAKE_PROFIT_PCT",  "value": 0.30 },
      { "type": "STOP_LOSS_PCT",    "value": 0.20 },
      { "type": "PRICE_ABOVE",      "value": 0.30 },
      { "type": "MAX_HOLD_MINUTES", "value": 120 },
      { "type": "RULE", "condition": { "feature": "spread_proxy", "op": "gt", "value": 0.05 } }
    ]
  },
  "constraints": {
    "max_open_positions": 5,
    "max_total_exposure_pct": 0.25,
    "max_positions_per_event": 1,
    "cooldown_minutes_per_market": 60,
    "allow_reentry_same_market": true
  }
}
```

Publish a JSON Schema at `packages/contracts/strategy.schema.json`. Validate on
save and on run. Reject unknown feature names — never silently ignore.

### 8.2 Feature registry

Each feature is a named pure function of the bar window. Every one must
document its null semantics, because null handling is where backtests lie.

| Feature | Params | Definition | Null when |
| --- | --- | --- | --- |
| `price` | — | Last observed price at or before `t` | No observation yet |
| `price_change_pct` | `lookback_minutes` | `(p_t / p_{t−L}) − 1` | Fewer than L minutes of history |
| `price_change_abs` | `lookback_minutes` | `p_t − p_{t−L}` in cents | same |
| `traded_volume` | `lookback_minutes` | Σ print size in window | No prints (returns 0, not null) |
| `volume_ratio` | `lookback_minutes`, `baseline_minutes` | window volume ÷ mean window volume over baseline | baseline shorter than required |
| `print_count` | `lookback_minutes` | number of prints | never (0) |
| `vwap_gap` | `lookback_minutes` | `p_t − vwap(window)` | no prints in window |
| `realized_vol` | `lookback_minutes` | stdev of 1-min log returns | insufficient bars |
| `time_to_resolution_minutes` | — | `scheduled_end − t` | `end_date` unknown |
| `time_since_open_minutes` | — | `t − open_ts` | never |
| `spread_proxy` | — | from recorded book if present, else `null` | no L2 data |
| `staleness_minutes` | — | minutes since last real observation | never |

**Null policy:** a comparison against a null feature evaluates to `false`. It
never coerces to 0. Count and surface `n_bars_skipped_null_feature` in data
quality — a strategy that never triggers because a feature is always null is a
bug the user must see, not an empty result.

### 8.3 Built-in strategies (v1)

Ship four, all editable, all labelled *Example strategy — not a trading
recommendation*:

1. **Underdog Probability Reversion** — as above.
2. **Favourite Drift** — buy ≥ 0.80 on a positive move, hold to resolution.
3. **Buy and Hold to Resolution** — buy at first qualifying bar, exit only at
   settlement. The honest benchmark for every other strategy.
4. **Random Entry** — same universe, same sizing, same constraints, entries
   drawn from a seeded RNG at the observed entry *rate* of the compared
   strategy. This is the null hypothesis (§15.3).

---

## 9. Backtest engine: the event loop

Single global chronological pass across all selected tokens. Not per-market
loops stitched together — a shared cash balance requires a shared clock.

```python
def run(request, dataset, settlement) -> BacktestResult:
    rng       = Random(request.seed)
    portfolio = Portfolio(request.starting_capital)
    clock     = MergedTimeline(dataset.bars, step=request.bar_interval)
    pending   = deque()   # signals awaiting decision-lag execution

    for t in clock:                                   # ascending, no gaps
        views = dataset.views_at(t)                   # look-ahead-safe (§5.2)

        # 1. MARK TO MARKET first, so exposure checks use current values
        portfolio.mark(t, views)

        # 2. SETTLEMENT of anything that resolved in (t-1, t]
        for pos in portfolio.open_positions():
            if settlement.resolved_between(pos.token, prev_t, t):
                portfolio.settle(pos, settlement.value(pos.token), t)

        # 3. EXITS before entries — frees capital in the same bar
        for pos in portfolio.open_positions():
            reason = strategy.evaluate_exit(views[pos.token], pos)
            if reason:
                pending.append(ExitSignal(pos, reason, signal_ts=t))

        # 4. ENTRIES
        for token, view in views.items():
            if not portfolio.can_open(token, view, request.constraints):
                continue
            decision = strategy.evaluate_entry(view)   # returns trace always
            if decision.enter:
                pending.append(EntrySignal(token, decision, signal_ts=t))

        # 5. EXECUTE signals whose lag has elapsed
        for sig in pending.due(t, request.decision_lag_bars):
            fill = execution.attempt(sig, dataset.at(t), request.execution)
            portfolio.apply(fill, t)                   # may record UNEXECUTABLE

        # 6. SNAPSHOT
        portfolio.record_equity(t)
        prev_t = t

    portfolio.force_settle_remaining(settlement, clock.end)
    return build_result(portfolio, request, dataset)
```

### 9.1 Ordering rules that must not change

- Mark-to-market **before** any decision, so `PCT_OF_EQUITY` sizing and exposure
  limits use the value at `t`, not `t−1`.
- Settlement **before** exits, so a resolved market cannot also trigger a
  take-profit in the same bar.
- Exits **before** entries, so freed cash is available in the same bar (this is
  realistic and it materially changes results at high exposure).
- Signals execute at `t + lag`, and the price used is the price at
  `t + lag`, not at `t`.

### 9.2 Timeline construction

- `bar_interval` default 1 minute; user may coarsen to 5m/15m/1h for speed.
  Coarsening aggregates from 1-minute data; it never re-requests coarse data.
- Only bars where at least one selected token has an observation are iterated.
- Runtime target: 1,000 markets × 90 days at 1-minute bars in under 60 seconds
  on a laptop. Achieve this with Polars columnar pre-computation of all features
  for all tokens up front, then a NumPy-indexed loop. Do not evaluate features
  row-by-row in Python.

---

## 10. Portfolio accounting

### 10.1 Ledger

Every cash movement is a row in an in-memory ledger with
`(ts, type, amount, running_cash, ref)` where type ∈
`{DEPOSIT, BUY, SELL, FEE, SETTLEMENT}`. The ledger is exportable and is the
audit trail behind the equity curve.

### 10.2 Invariants — assert these at every bar in debug builds

```
cash >= 0                                            # never spend money you don't have
equity == cash + Σ(shares_i × price_i)
Σ(shares_i × price_i) <= max_total_exposure_pct × equity   # at time of entry
starting_capital + Σ(realized_pnl) - Σ(fees) == cash + Σ(cost_basis)   # closed-form check
open_positions <= max_open_positions
```

A violated invariant fails the run loudly. It does not clamp and continue.

### 10.3 Sizing modes

| Mode | Notional | Notes |
| --- | --- | --- |
| `FIXED_CASH` | `value` | No compounding. |
| `PCT_OF_EQUITY` | `value × equity(t)` | Compounding. Equity includes unrealized. |
| `PCT_OF_INITIAL` | `value × starting_capital` | Fixed fractional. |
| `FIXED_SHARES` | `value × fill_price` | Notional varies with price. |

After computing notional, clamp by: available cash, remaining exposure headroom,
`max_notional_per_trade`, and available liquidity (§11.3). Record which
constraint bound the size in `entry_rule_trace.size_binding_constraint` — users
need to know when their strategy was capital-constrained rather than
signal-constrained.

Shares = `floor(notional / fill_price, share_precision)`. If shares <
`min_order_size`, the trade is `REJECTED_MIN_SIZE`. **This matters enormously
for the $100 bankroll case**: a $5 position at $0.02 is 250 shares (fine), but a
$5 position at $0.95 is 5.26 shares, which may be below a market's minimum. The
UI must surface a "trades rejected for minimum size: N" counter, otherwise small
bankrolls look artificially clean.

### 10.4 Settlement

On resolution, winning shares credit `shares × 1.00`, losing shares credit
`0.00`, invalid credits `shares × 0.50`. No exit fee is charged at settlement
(Polymarket does not charge on winnings) — but this is a factual assumption;
record it in `docs/DATA_NOTES.md` with a source and make it a config flag.

---

## 11. Execution models

### 11.1 Shared pipeline

```
reference_price  →  model adjustment  →  slippage  →  tick quantisation  →  bounds  →  fee
```

- Bounds: fill price clamped to `[tick_size, 1 − tick_size]`.
- Quantisation: `round_half_even(price / tick) × tick`, rounded **against** the
  trader (up for buys, down for sells).
- Fee computed on the quantised price with the θ resolved per §3.7.

### 11.2 `LAST_PRICE`

`reference_price` = bar price at execution timestamp. Fastest, most optimistic.
Header badge: `OPTIMISTIC — assumes fill at mid, unlimited size`.

### 11.3 `TRADE_PRINT` (default)

Uses `trade_prints` in the execution bar:

1. **Reference price**: VWAP of prints in the bar, or if none, the bar price
   with `is_stale` inherited.
2. **Liquidity cap**: `max_shares = liquidity_participation × Σ(print size in
   bar)`. Default `liquidity_participation = 0.10`. If the desired size exceeds
   the cap:
   - `partial_fill_policy = ALLOW` → fill `max_shares`, status `PARTIAL`;
   - `partial_fill_policy = REJECT` → status `UNEXECUTABLE`, no position.
3. **No prints in bar** → status `UNEXECUTABLE` unless
   `allow_fill_on_no_prints` is on, in which case fall back to `LAST_PRICE`
   with a doubled slippage penalty and flag the trade.
4. **Adverse-selection penalty** (optional, on in the Conservative preset): fill
   at the worst print price in the bar rather than VWAP.

Every `UNEXECUTABLE` is recorded as a row in `bt_trades` with
`fill_status='UNEXECUTABLE'` and zero P&L. They are counted and displayed. A
strategy whose signals were 60% unexecutable is not a 40%-as-good strategy; it
is a strategy that does not work at that size, and the UI must say so.

### 11.4 `BOOK` (v2, gated)

Walk recorded L2 levels, consuming depth, computing the true average fill price.
Available only where `orderbook_snapshots` covers the timestamp. Selecting it
for an uncovered period is a validation error, not a silent fallback.

### 11.5 Slippage

`slippage_bps` applied against the trader: buys fill at
`ref × (1 + bps/10_000)`, sells at `ref × (1 − bps/10_000)`. Note in the UI that
in a 0–1 price space, proportional slippage is harsher at low prices in
probability terms; also offer `slippage_ticks` (absolute) which is the more
natural unit here. Default: 1 tick.

### 11.6 Scenario presets

| Preset | Model | Slippage | Participation | Partial | No-print fallback | Fees |
| --- | --- | --- | --- | --- | --- | --- |
| Optimistic | `LAST_PRICE` | 0 | ∞ | n/a | allowed | historical |
| Realistic | `TRADE_PRINT` | 1 tick | 0.10 | allow | off | historical |
| Conservative | `TRADE_PRINT` | 2 ticks + worst-print | 0.05 | reject | off | current |

"Run all three" is a first-class button. The default result view shows
**Realistic**, with Optimistic and Conservative as secondary columns. Never show
Optimistic alone.

---

## 12. Metrics — exact definitions

Ambiguous metrics are how backtesting tools mislead. Define each one and show
the definition in a tooltip.

### 12.1 Returns

- `net_profit = ending_equity − starting_capital`
- `roi = net_profit / starting_capital`
- `cagr` computed only if the span ≥ 90 days; otherwise render `—` with
  "period too short to annualise". **Never annualise a 3-week backtest.**

### 12.2 Risk

- `max_drawdown` computed on the **mark-to-market equity curve including
  unrealized P&L**, sampled at `bar_interval`. Report both the dollar and
  percentage figure and the peak/trough timestamps and duration.
- `sharpe`: computed on **daily** equity returns, `mean/stdev × √252`,
  risk-free = 0. Suppress and show `insufficient data` when fewer than 30 daily
  observations. Add a tooltip: prediction-market returns are highly non-normal
  and lumpy; Sharpe here is indicative only.
- `sortino`: same but downside deviation.
- Also report `max_consecutive_losses` and `worst_trade` — more intuitive than
  Sharpe for this asset class.

### 12.3 Trade statistics

- `win_rate = wins / closed_trades` where a win is `net_pnl > 0`. Excludes
  `UNEXECUTABLE` and `REJECTED_MIN_SIZE`. Show the excluded count next to it.
- `profit_factor = Σ gross profit / |Σ gross loss|`. If there are no losing
  trades, render `∞` and a low-sample warning — do not emit a huge float.
- `expectancy = mean(net_pnl per trade)`, with a bootstrap 95% CI (§15.2).
- `avg_holding_time`, median as well as mean (the distribution is skewed).
- Full P&L histogram, not just averages.

### 12.4 Exit-reason enum

`TAKE_PROFIT | STOP_LOSS | MAX_HOLD | PRICE_TARGET | RULE_EXIT |
MARKET_RESOLVED | END_OF_BACKTEST | FORCED_EXPOSURE_LIMIT | DATA_GAP`

Every closed trade has exactly one. The results page shows the exit-reason
breakdown as a bar chart — a strategy where 70% of exits are `END_OF_BACKTEST`
is measuring something different from what the user thinks.

### 12.5 Cost attribution — always shown, never collapsed

```
Gross P&L        +$61.40
Fees              −$6.20
Slippage          −$7.40
────────────────────────
Net P&L          +$47.80
```

Plus: `cost_ratio = (fees + slippage) / |gross_pnl|`. When this exceeds 0.5, the
results header shows: *"Costs consumed more than half the gross edge. This
strategy is highly sensitive to execution assumptions."*

---

## 13. Trade replay and explanation

This was correctly identified as extremely important in v1. Specification:

### 13.1 Rule trace

`evaluate_entry` **always** returns a trace, whether or not it fires:

```jsonc
{
  "ts": "2026-06-12T14:32:00Z",
  "decision": "ENTER",
  "clauses": [
    {"feature":"price","op":"lte","threshold":0.20,"actual":0.18,"passed":true},
    {"feature":"price_change_pct","params":{"lookback_minutes":10},
     "op":"gte","threshold":0.03,"actual":0.047,"passed":true},
    {"feature":"traded_volume","params":{"lookback_minutes":10},
     "op":"gte","threshold":50,"actual":120,"passed":true}
  ],
  "sizing": {"mode":"PCT_OF_EQUITY","requested_notional":5.24,
             "final_notional":5.24,"size_binding_constraint":null},
  "execution": {"model":"TRADE_PRINT","ref_price":0.180,"vwap":0.181,
                "slippage_ticks":1,"fill_price":0.191,
                "bar_print_volume":120,"cap":12.0,"shares":27.4,
                "fee_theta":0.05,"fee":0.21,"status":"FILLED"}
}
```

The UI renders this as the checklist from v1 §28, but generated from data rather
than hand-written prose.

### 13.2 Near-miss inspector

Store traces for the top-N non-firing bars per market (`decision:"REJECT"`,
capped, sampled) so the user can ask "why didn't it trade here?" This is the
feature that makes strategy iteration fast, and no competing tool does it well.

### 13.3 Replay chart

Price series ±2 hours around the trade, with: entry and exit markers, individual
trade prints as dots sized by volume, shaded lookback window for each triggering
feature, the exit thresholds as horizontal lines, and resolution time.

---

## 14. HTTP API

```
GET    /api/markets?q=&category=&from=&to=&min_volume_asof=&cursor=
GET    /api/markets/{condition_id}
GET    /api/markets/{condition_id}/series?token=&from=&to=&fidelity=
GET    /api/collections                       -> curated market sets
POST   /api/collections                        { name, filter | condition_ids[] }

GET    /api/strategies
POST   /api/strategies                         { name, dsl }        -> v1
POST   /api/strategies/{id}/versions           { dsl }              -> v(n+1)
POST   /api/strategies/validate                { dsl } -> { ok, errors[] }

POST   /api/backtests                          { BacktestRequest } -> { run_id }
GET    /api/backtests/{run_id}                 -> status + metrics + data_quality
GET    /api/backtests/{run_id}/stream          -> SSE progress
GET    /api/backtests/{run_id}/equity?from=&to=&max_points=
GET    /api/backtests/{run_id}/trades?filters&cursor
GET    /api/backtests/{run_id}/trades/{seq}/replay
GET    /api/backtests/{run_id}/export.csv|.json
POST   /api/backtests/{run_id}/rerun           { overrides } -> new run_id
POST   /api/compare                            { run_ids[] }
GET    /api/dataset/versions
```

`BacktestRequest` is one Pydantic model, hashed to `config_hash`. Rerun with
overrides always creates a new run. Nothing is ever mutated.

Equity endpoint downsamples server-side via LTTB to `max_points` (default 2000)
— never ship 500k points to the browser.

---

## 15. Statistical honesty layer

This is a product differentiator, not a disclaimer.

### 15.1 Sample-size gate

`n_trades < 30` → results render behind a banner: *"Too few trades to
distinguish this from luck."* Metrics still shown, but greyed with the warning
pinned above the equity curve.

### 15.2 Bootstrap confidence intervals

Resample closed trades with replacement 10,000 times (seeded). Report the 5th
and 95th percentile of final ROI. Display as `ROI +47.8% (90% CI: −12.4% to
+108.2%)`. Most retail-looking backtests die honestly right here, and that is
the point.

### 15.3 Null-hypothesis comparison

Run automatically alongside every backtest, using the same seed family:

- **Random-entry baseline** — same universe, same entry *rate*, same sizing,
  same execution assumptions, random timestamps.
- **Timestamp permutation test** — hold entry times fixed, shuffle which market
  each signal maps to (within category), 200 iterations. Report the percentile
  rank of the real strategy's ROI. This is a stronger test than a random
  baseline because it preserves the market-selection distribution.
- **Buy-and-hold-to-resolution** on the same universe.

Results header shows: `Your strategy beat 62% of random variants` — with the
plain reading that anything under ~95 is not evidence of edge.

### 15.4 Multiple-comparisons accounting (when optimization lands in v2)

Track how many parameter combinations the user has run against the same data
slice, per strategy family, and display a running count: *"You have tested 214
configurations on this period. The best of 214 random strategies would be
expected to show roughly +X% ROI by chance."* Compute X from the permutation
distribution.

---

## 16. Data-quality panel (shown on every result, not hidden in settings)

Rendered from `backtest_runs.data_quality`:

```jsonc
{
  "dataset_version": "…", "coverage": {"start":"…","end":"…"},
  "markets_selected": 104, "markets_with_usable_data": 97,
  "markets_dropped": [{"condition_id":"…","reason":"NO_PRICE_HISTORY"}],
  "bars_total": 1284000, "bars_stale": 41200, "stale_pct": 3.2,
  "median_gap_minutes": 1, "p95_gap_minutes": 14,
  "prints_total": 88213, "bars_with_no_prints_pct": 61.4,
  "orderbook_coverage_pct": 0.0,
  "signals_generated": 143, "signals_filled": 83,
  "signals_unexecutable": 47, "signals_rejected_min_size": 13,
  "bars_skipped_null_feature": 8811,
  "fee_mode": "HISTORICAL", "fee_schedules_applied": ["2024-fee-free","2026-03-v2"]
}
```

`signals_generated` vs `signals_filled` is the most informative single ratio in
the product. Put it on the results summary card.

---

## 17. Testing strategy

### 17.1 Golden synthetic markets

Hand-built fixtures with analytically known answers. Each is a small CSV plus an
expected-result JSON, asserted to the cent.

| Fixture | Asserts |
| --- | --- |
| `single_trade_tp` | One entry, take-profit hit, exact fee and slippage arithmetic |
| `single_trade_resolution` | Position held to resolution, settles at $1.00 |
| `losing_resolution` | Settles at $0.00, equity drops by full notional |
| `invalid_resolution` | Settles at $0.50 |
| `cash_constrained` | Two simultaneous signals, only one affordable; second rejected |
| `exposure_capped` | 25% cap binds; size reduced and reason recorded |
| `min_size_rejected` | High-price small-notional order rejected |
| `no_liquidity` | Zero prints in bar → `UNEXECUTABLE` |
| `partial_fill` | Participation cap binds, `PARTIAL`, correct share count |
| `compounding` | 5% of equity across 3 trades matches hand-computed 5.00, 5.25, … |
| `fee_schedule_change` | Same trade on either side of an effective date gets different fees |
| `stale_data` | Gap > threshold flags trade and blocks entry |

### 17.2 Property tests (Hypothesis)

- Cash is never negative, for any random strategy/config.
- `equity == cash + Σ position values` at every bar.
- Number of `bt_trades` rows with `fill_status='FILLED'` equals the number of
  BUY ledger entries.
- Reversing the order of markets in the request does not change any metric
  (order-independence).

### 17.3 Determinism

Same `(config_hash, dataset_version, seed, engine_version)` → byte-identical
`bt_trades` and `bt_equity`. Asserted in CI over three fixture configs.

### 17.4 Look-ahead adversarial tests

A `ClairvoyantStrategy` that attempts each of: `bars[+1]`, reading
`market.resolution`, reading `final_volume`, reading a future bar via the
features mapping. Each must raise. This test file is the product's conscience —
mark it as such in a comment so nobody deletes it to make CI green.

### 17.5 Reconciliation

For a handful of real historical markets, reconcile ingested price bars against
independently fetched trade prints: assert bar prices lie within the
[min print, max print] envelope of their window ≥ 95% of the time. Log
violations to `docs/DATA_NOTES.md`.

---

## 18. Milestones

### M0 — Skeleton (acceptance: `make dev` runs, CI green)
Monorepo, Postgres + Timescale + Redis via docker-compose, migrations, FastAPI
health check, Next.js shell, CI running lint + tests + the no-I/O-in-engine
check.

### M1 — Data (acceptance: 500+ markets and their full 1-minute price history in Postgres, `make rebuild-normalized` reproduces the normalized layer bit-identically)
Gamma market/event ingestion, CLOB `prices-history` chunked backfill, Data API
trade prints, `fee_schedules` seeded from docs with source URLs, dataset
versioning, data-quality report CLI. **Start the recorder service here.**

### M2 — Engine (acceptance: all §17.1 golden tests pass, §17.4 leakage tests raise, determinism test passes)
DSL schema + parser + feature registry, `MarketView`, event loop, portfolio and
ledger, `LAST_PRICE` and `TRADE_PRINT` execution, fee and slippage, settlement,
metrics. Driven entirely from a CLI: `strategy-lab run config.json`. No UI.

### M3 — API + minimal UI (acceptance: a user can run the Underdog strategy on one collection from the browser and see the equity curve, blotter, and data-quality panel)
Backtest queue, SSE progress, results endpoints, three screens: Simulator
wizard, Results, Trade blotter.

### M4 — Explanation (acceptance: every trade in the blotter opens a replay with a fully data-driven rule checklist)
Rule traces, near-miss inspector, replay chart, exit-reason breakdown.

### M5 — Honesty (acceptance: every result renders CI, permutation percentile, and baseline comparison)
Bootstrap CIs, random baseline, permutation test, buy-and-hold benchmark,
scenario preset triple-run, cost attribution, low-sample gating.

### M6 — Iteration (acceptance: a user can fork a config, rerun, and diff two runs side by side)
Strategy builder UI, versioning and diff, run comparison, CSV/JSON export,
research report generation.

**Then** v2: order-book execution over recorded data, parameter sweeps with OOS
splits and multiple-comparison accounting, walk-forward, category analysis.

---

## 19. Frontend notes

Route map: `/` dashboard · `/markets` · `/strategies` · `/strategies/[id]` ·
`/simulate` (wizard) · `/runs` · `/runs/[id]` · `/runs/[id]/trades/[seq]` ·
`/compare` · `/data` · `/settings`.

Design direction: quantitative research terminal. Dark, near-neutral background
(not pure black), one restrained accent, monospaced tabular numerals for every
figure, generous data density, no card shadows, no gradients on data surfaces.
Gains and losses distinguished by more than hue (sign + weight) for
accessibility. No animation on numeric change beyond a 120ms fade.

Every screen needs designed loading, empty, partial, and error states.
"Partial" matters specifically: a run that completed with 40% unexecutable
signals is not an error and not a clean success, and the UI must have a visual
language for it.

Required copy, persistent on every results view:

> **SIMULATED RESULTS — NOT ACTUAL TRADING PERFORMANCE.** Backtested returns are
> hypothetical, rely on the execution assumptions shown below, and do not
> account for factors that affect real trading. Past performance does not
> indicate future results. This tool is for research and is not financial advice.

---

## 20. Open decisions for the product owner

The agent should implement the stated default and flag these for review:

1. **Venue.** International Polymarket or the US exchange? Fee schedules and
   available markets differ. *Default: international.*
2. **Bankroll realism.** At $100 with a $0.02 tick, many strategies are
   dominated by minimum-size rejections. Should the default preset warn and
   suggest $1,000? *Default: yes, a non-blocking hint.*
3. **Multi-outcome events.** One position per event, or allow several?
   *Default: one, configurable.*
4. **Backtest history retention.** Full `bt_trades` for every run grows fast.
   *Default: keep all; add a retention setting later.*
5. **Self-hosted vs deployed.** Ingestion volume and rate limits make a hosted
   multi-tenant version materially different. *Default: single-user
   self-hosted for v1.*
6. **Legal.** Prediction-market access and promotion is jurisdiction-dependent
   and actively litigated in parts of the US. A research/backtesting tool is a
   different posture from a trading tool, but if this is ever distributed
   publicly, get local advice for Ghana and for any market you'd distribute in.
   Nothing in this document is legal advice.

---

## Appendix A — Kickoff prompt for Claude Code

> Read `polymarket-strategy-lab-prd-v2.md` in full before writing code.
> Implement milestone M0 only, then stop and summarise.
> Constraints you must not violate: `packages/engine` performs no I/O; the
> strategy evaluator cannot access future bars or resolution data; no metric is
> computed client-side; unavailable data produces `UNEXECUTABLE`, never an
> interpolated value.
> Before M1, verify every Polymarket endpoint and the current fee schedule
> against the live docs and record any discrepancy with this document in
> `docs/DATA_NOTES.md`.

## Appendix B — Sources to verify at build time

- API base URLs and endpoints: `https://docs.polymarket.com/quickstart/reference/endpoints`
- Price history spec: `https://docs.polymarket.com/api-reference/markets/get-prices-history`
- Batch price history: `https://docs.polymarket.com/api-reference/markets/get-batch-prices-history`
- Full docs index for the agent: `https://docs.polymarket.com/llms.txt`
- Trading fees: `https://help.polymarket.com/en/articles/13364478-trading-fees`
- US venue fee schedule: `https://docs.polymarket.us/fees`
- Python client: `https://github.com/Polymarket/py-clob-client`

Fee rates and endpoint shapes change. Treat all of the above as inputs to
verify, not as constants to hardcode.
