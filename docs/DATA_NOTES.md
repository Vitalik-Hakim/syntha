# Data notes

Discrepancies between `docs/PRD.md` and reality (live APIs, database constraints), and factual
assumptions the engine depends on. Per PRD §0 rule 4, reality wins; each entry says what we did.

Status legend: **resolved** (implemented as described), **open** (must be settled in the named
milestone), **finding** (verified fact that later work must respect).

Verification sources are cited inline. "Live probe" means a real request made on 2026-09-14
against production endpoints.

---

## Pre-M1 API verification (PRD Appendix A) — done 2026-09-14

### Summary of what changes versus the PRD

| PRD says | Reality | Consequence |
| --- | --- | --- |
| `prices-history` is a smoothed trade series | It is a **per-minute sample of a mid-like price**, not trades (details below) | Bars feed `LAST_PRICE` only. Trade prints are the only execution truth. §17.5's 95% envelope check cannot pass as written. |
| Fetch narrow windows at `fidelity=1` | Correct for CLOB v1, **max window 15 days** | Backfill in 15-day chunks |
| 1-minute history is retroactively available | True on CLOB v1 today (back to at least Jan 2024). The newer Data API v2 keeps 1-minute data only **≥7 days** | Backfill as early as possible; treat `raw/` as the permanent copy |
| Trades keyed by `condition_id`, v1 `/trades` | v1 `offset` is capped at 10,000. Use **`/v2/trades`** keyset pagination | Full print history is reachable |
| Fee = `C × θ × p(1−p)`, θ by category and date | Fee = `C × rate × (p(1−p))^exponent`, **per market** (`feeSchedule`), versioned by `feeType` | `fee_schedules` is keyed by `fee_type`, not category |
| Fees rounded to cents, banker's rounding | **Rounded to 5 decimal places**; minimum fee 0.00001 | Engine rounds to 5 dp |
| `category` on the market | No category field on markets or events; only event **tags** and market `feeType` | Category derives from `feeType`, falling back to tags |
| `retry_after_seconds` in error body | Data API sends a **`Retry-After` header**; the body field exists only on CLOB trading errors | Honour the header |
| Websocket `wss://…/ws/` with channel `market` | `wss://ws-subscriptions-clob.polymarket.com/ws/market`, app-level `PING` every 10s | Recorder uses this URL |

### Gamma API — finding

Source: live probes of `GET /markets`, `GET /events/{id}`; Polymarket changelog
(`docs.polymarket.com/changelog/predictions.md`).

- `GET /markets` defaults to `closed=false` since 2026-04-09. Always pass `closed=true` for
  history. The page size is capped at 100.
- `GET /markets/keyset` (`after_cursor` / `next_cursor`, max limit 100) replaces offset paging.
  The offset endpoints are slated for deprecation.
- Fields used by ingestion: `conditionId`, `questionID`, `slug`, `question`, `outcomes` and
  `clobTokenIds` (both **JSON-encoded strings**, not arrays), `orderMinSize`,
  `orderPriceMinTickSize`, `negRisk`, `feesEnabled`, `feeType`, `feeSchedule`, `startDate`,
  `acceptingOrdersTimestamp`, `endDate`, `closedTime`, `umaResolutionStatus`, `outcomePrices`,
  `volumeNum`, `events[]`.
- **Only one minimum order size** (`orderMinSize`) is exposed. The maker/taker split in PRD §3.6
  isn't available, so `min_order_size` stays a single column. Resolves the M0 open item.
- Tick sizes finer than a cent exist (0.001; World Cup markets 0.0025 since 2026-07-02), and
  tick size can change during a market's life (`tick_size_change` websocket event). The
  `numeric(8,6)` price type is sufficient. Historical tick-size changes are not retrievable;
  only the current value is.
- `closedTime` is **not** the resolution time. Example: 2024 US presidential election market,
  `closedTime` 2024-11-06 15:17 UTC vs on-chain resolution 14:14 UTC (`/v2/resolutions`).

### Price history — finding (most important)

Source: live probes of `GET clob.polymarket.com/prices-history` and
`GET data-api.polymarket.com/v2/prices-history`, plus the v2 OpenAPI description.

- **CLOB v1** `prices-history?market=<token>&startTs&endTs&fidelity=1`:
  - Returns a point for **every minute**, including minutes with no trades (1,440 points per
    day), timestamped a few seconds after the minute boundary.
  - Maximum window is **15 days** (16 days → `"'startTs' and 'endTs' interval is too long"`).
    `interval=max` with `fidelity=1` returns an empty history.
  - Works for old markets: 1-minute data came back for January 2024 and March 2026.
- **What the price is:** on a 0.001-tick market (Becerra CA primary, 2026-06-01), 891 of 1,440
  bars were **off the tick grid** (e.g. 0.8945), bar changes happened in minutes with no trades,
  and bars were a median of 3.4 ticks from same-minute trade prices (32% within 1 tick). The
  series behaves like a sampled midpoint, not a trade price. Consequences:
  - `price_bars` cannot tell us when a market was quiet. `is_stale` / `staleness_minutes`
    **must be derived from trade prints**, not from gaps in the bar series (there are none).
  - PRD §17.5 (bar price inside the print envelope ≥95%) would fail by construction. M1's
    reconciliation report instead measures bar-to-print distance in ticks and reports it as a
    data-quality statistic, without a pass/fail threshold. Revisit in M2 if the engine needs one.
  - `LAST_PRICE` fills at this mid-like price, which matches the PRD's badge text
    ("assumes fill at mid").
- **Data API v2** `/v2/prices-history`: explicit windows cap at 15 days; **1-minute data is kept
  at least 7 days, 5-minute ≥60 days, 30-minute ≥90 days; only 3-hour and 12-hour grains are
  permanent** (back to 2022-11-18). Requesting an expired grain returns an empty page, not an
  error. v2 is the direction Polymarket is moving; if v1 adopts the same retention, historical
  1-minute bars stop being obtainable. **Backfill early and never delete `raw/`.**

### Trade prints — finding

Source: live probes of `/v2/trades`; v1 and v2 OpenAPI specs; rate-limits page.

- v1 `GET /trades` caps `offset` at 10,000, so deep history on busy markets is unreachable.
- `GET /v2/trades?condition=<id>&limit=1000` uses keyset pagination with no depth limit, newest
  first. The `condition` filter serves a **fixed three-year window**; `start`/`end` are ignored
  for it.
- **The cursor does not carry filters.** Re-send `condition` on every page. A page requested
  with only `cursor` returns `data: []` and `has_more: false`, which is indistinguishable from
  the end of the data. The endpoint description implies otherwise; the API conventions section
  states it. The ingester must always re-send filters, and has a test for it.
- `taker_only` defaults to `true`: each fill appears once, on the taker side, and `side` is the
  taker's side. This is the PRD's intended semantics.
- Fields: `token_id`, `condition_id`, `side`, `size` (shares), `price`, `timestamp` (block time,
  seconds), `transaction_hash`, `outcome_index`, `proxy_wallet`, plus profile fields we discard.
- **Natural key:** no fill id or log index. In the probed markets `transaction_hash` was unique
  per taker print (1,792 and 1,000 rows, zero repeats), but a transaction can in principle
  contain several fills. Dedup key: `(transaction_hash, token_id, proxy_wallet, side, price,
  size, ts)`, stored as a hash column with a unique index including `ts`. Resolves the M0 open
  item.
- Trade `price` is not tick-aligned (e.g. 0.8905578). It appears to be computed from USDC and
  share amounts. `numeric(8,6)` rounds it; the raw value is kept in `raw/`.
- Timestamps are seconds, so prints within the same second can't be ordered. Pagination order
  within a second follows the server's internal sequence.

### YES/NO complement on bars carries no information — finding

Source: normalized smoke dataset (13 markets, 248k paired bars), 2026-09-14.

On CLOB bars, `price(YES) + price(NO)` was **exactly 1.000000 for 99.2% of paired minutes**.
Both tokens trade on one shared book (a NO bid is a YES ask), so the two mid-like series are
mirror images. PRD §3.3's divergence metric is therefore measured on **trade prints**
(same-minute average taker price per side, median deviation 0.01 in the smoke set). The
bar-based number is still reported so this finding stays visible.

### Resolution — finding

Source: `docs.polymarket.com/concepts/resolution.md`; live probe of `/v2/resolutions`.

- `GET /v2/resolutions?condition=<ids>` (≤20 per call) returns `status`, `price` (1e18 = YES,
  0 = NO, 5e17 = 50/50), `was_disputed`, `last_update_timestamp`. For resolved markets,
  `last_update_timestamp` is the resolution time used as `resolved_at`.
- **50/50 ("Unknown") resolutions exist and redeem at $0.50 per token**, per the resolution
  docs. That confirms the PRD's `INVALID → 0.50`. A live 50/50 example has not been probed
  yet; the ingester maps `price == 5e17` to `INVALID` and fails loudly on any other value.
- Row shape varies: some condition lookups return the question-keyed shape
  (`last_update_timestamp` in epoch seconds) instead of RFC3339. Handle both.
- Some rows carry sentinel values (e.g. `reproposed_price: "69"`). Only `status` and `price`
  are trusted.
- Resolution takes about 2 hours undisputed and 4–6 days disputed. That's the gap between
  `end_date` and `resolved_at` the PRD mentions.

### Fees — finding

Sources: `docs.polymarket.com/trading/fees.md` (current rates, formula, 5 dp rounding),
`docs.polymarket.com/changelog/predictions.md` (dated rollout), live probes of `feeType` /
`feeSchedule` across market eras.

**Model.** Each fee-enabled market has
`feeSchedule = {rate, exponent, takerOnly, rebateRate}` and a `feeType` string. The fee is
`C × rate × (p × (1 − p))^exponent`, rounded to 5 decimal places. Makers pay nothing. No fee
at redemption: fees are "applied at match time", and redemption is not a trade.

Observed `feeType` values and schedules:

| feeType | rate | exponent | Observed on |
| --- | --- | --- | --- |
| `crypto_15_min` | 0.25 | 2 | 15-minute crypto, Jan 2026 (peak 1.5625¢/share, matching the changelog's "1.56%") |
| `crypto_fees_v2` | 0.07 | 1 | crypto, Jul 2026 |
| `sports_fees_v2` | 0.03 | 1 | sports markets created before 2026-07-10, still 0.03 after |
| `sports_fees_v3` | 0.05 | 1 | sports markets created after 2026-07-10 |
| `politics_fees` | 0.04 | 1 | |
| `finance_prices_fees` | 0.04 | 1 | |
| `economics_fees` | 0.05 | 1 | |
| `culture_fees` | 0.05 | 1 | |
| `general_fees` | 0.05 | 1 | |
| (null, `feesEnabled=false`) | 0 | — | geopolitics; all markets closed before their category's rollout |

**Rollout dates (changelog):**

| Date | Change |
| --- | --- |
| before 2026-01-05 | No taker fees anywhere |
| 2026-01-05 | 15-minute crypto markets |
| 2026-02-12 | 5-minute crypto markets launch with fees |
| 2026-02-18 | NCAAB and Serie A sports markets |
| 2026-03-06 | All crypto markets, **new markets created after this date only** |
| 2026-03-30 | Fee Structure V2: all categories except geopolitics |
| 2026-03-31 | `feeSchedule` object becomes the fee source of truth |
| 2026-07-10 | Sports rate 0.03 → 0.05. In practice applied to newly created sports markets (`sports_fees_v3`); older sports markets stayed on `sports_fees_v2` |

**What Gamma does not tell us:** `feeSchedule` is the market's **current** schedule, not its
history. A market created in 2025 and still open on 2026-03-30 (e.g. "Argentina wins the 2026
World Cup") now shows `sports_fees_v2`, but it traded fee-free for most of its life. Fees on
such markets are resolved as `feeSchedule.rate` only for fills on or after the `effective_from`
of their `feeType` (table above), and zero before. This is an inference from the changelog, not
a per-fill record; the Data API trade payload carries no fee field. Every backtest using
`HISTORICAL` fees shows this as an assumption in the run's data-quality block.

Secondary coverage (e.g. [Crowdfund Insider, 2026-03-25](https://www.crowdfundinsider.com/2026/03/268884-polymarket-to-impose-taker-fees-on-nearly-all-trading-categories/))
matches the 2026-03-30 date. It also reports sports at 0.03 under V2, matching
`sports_fees_v2`. Today's fees page shows 0.05 because it describes v3.

### Rate limits — finding

Source: `docs.polymarket.com/api-reference/rate-limits.md`.

Cloudflare, IP-based, sliding windows. Excess requests are **throttled (queued), not rejected**,
though Data API v2 returns `429` + `Retry-After` when its query pool is saturated. Limits that
matter for ingestion: CLOB `/prices-history` 1,000/10s; Data v2 `/v2/trades` 300/10s and
`/v2/prices-history` 200/10s; Gamma `/markets` 300/10s. The PRD's conservative 5 req/s default
sits far below every limit and is kept.

### Websocket — finding

Source: `docs.polymarket.com/market-data/realtime-data.md`.

URL `wss://ws-subscriptions-clob.polymarket.com/ws/market`. Subscribe with
`{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}`; add or remove tokens
with `{"assets_ids": [...], "operation": "subscribe" | "unsubscribe"}`. Send text `PING` every
10s. Wire format (live capture): the initial snapshot is a **JSON array** of `book` events; later
frames are single objects. `book` levels arrive **bids ascending, asks descending** (worst price
first), which is the reverse of PRD §7's storage convention, so the recorder re-sorts them.
`new_market` events arrive for markets that were never subscribed. Events: `book` (full snapshot), `price_change` (level deltas with best bid/ask),
`last_trade_price` (includes `fee_rate_bps`, the only per-fill fee record anywhere),
`tick_size_change`, `best_bid_ask`, `new_market`, `market_resolved`. Prices and sizes are
decimal strings.

---

## Schema

### trade_prints primary key is `(id, ts)`, not `(id)` — resolved (M0)

PRD §7 declares `id bigserial PRIMARY KEY` and makes `trade_prints` a Timescale hypertable
partitioned on `ts`. Timescale rejects unique indexes that don't include the partitioning column,
so the PK is `(id, ts)`. `id` is still a `bigserial`.

### `fee_schedules.notes` added — resolved (M0)

§3.7 lists a `notes` column; the §7 DDL omits it. The column exists (nullable).

### Hypertables created without Timescale's default `ts` index — resolved (M0)

`create_hypertable(..., create_default_indexes => FALSE)`. Every index is declared explicitly
in the migration, so the ORM models and the migration can be diffed exactly
(`packages/storage/tests/test_migrations.py`). Time-range scans still get chunk exclusion.
Revisit in M1 if cross-token time scans turn out slow.

### Recorder storage — resolved (M1, migrations 0002–0003)

`orderbook_snapshots` (one row per `book` event) and the new `orderbook_deltas` (one row per
`price_change` level) are hypertables with 1-day chunks, compressed after 2 days (segmented by
token). Measured volume: 10 tokens produced about 2,600 delta rows and 1 MB of raw frames in
40 seconds, roughly 5.6M rows and 2.3 GB of uncompressed frames per day. Raw frames are written
gzip-compressed. The default watchlist is the top 10 markets by 24h volume; widen it
deliberately.

### `trade_prints` natural key — resolved (M1, migration 0002)

`print_key` (sha256 of the dedup tuple under Trade prints) with a unique index on
`(print_key, ts)`.

### Fee schedule shape — resolved (M1, migration 0002)

`fee_schedules` is keyed by `(venue, fee_type, effective_from)` with `rate`, `exponent`,
`applies_to` (`ALL_OPEN_MARKETS` | `MARKETS_CREATED_AFTER`) and `source_url`, seeded from
`services/ingestion/src/syntha_ingestion/seeds/fee_schedules.json`. `markets` carries its own
`fee_type`, `fee_rate`, `fee_exponent`. Where a type's start date is ambiguous the earliest
documented date is used (overstates fees; conservative). The data-quality report flags fee types
without a schedule and rate mismatches.

### Normalized layer is single-version — open (M3)

`make rebuild-normalized` replaces the normalized tables in place. Each build records a
`dataset_versions` row with its content hash, but older versions' rows are not kept. A backtest
run that references an older `dataset_version_id` can detect the mismatch (`syntha-ingest
verify`) but can't be re-run on the old data without rebuilding from the same raw inputs. Decide
in M3 whether to keep versioned snapshots (e.g. a schema per version) once backtests are
persisted.

### Token ids stored as text on every bar — open (M2)

`clob_token_id` is a 77-digit decimal string repeated on every `price_bars` row, the dominant
storage cost. Consider an integer surrogate key or Timescale compression on `price_bars`
once the engine's read patterns are known.

### Maker vs taker minimum order size — resolved (pre-M1)

Only `orderMinSize` exists in Gamma and CLOB. Single column kept.

### Table names in PRD §0 — noted

§0 rule 3 refers to `trades` and `equity_curve`; the schema names them `bt_trades` and
`bt_equity`. The schema names are used.

---

## Assumptions the engine relies on

| Assumption | Source | Confidence |
| --- | --- | --- |
| No fee at redemption/settlement (§10.4) | fees.md: fees "applied at match time"; resolution.md: redemption burns tokens for collateral with no fee mentioned | High |
| 50/50 resolution pays $0.50 per token | resolution.md | High |
| Mid-life fee activation follows the `feeType` rollout date | changelog + `feeSchedule` observations | Medium (inference) |
| `/v2/resolutions` `last_update_timestamp` is the resolution time for resolved rows | spec: "Latest lifecycle change" | Medium-high |
| v1 bars are mid-like samples | live reconciliation on one market | Medium (one market; M1 reconciliation report widens the sample) |
