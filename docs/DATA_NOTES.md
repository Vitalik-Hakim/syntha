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
- **`GET /markets/{id}` omits `events`**; the listing endpoints include it. Full objects for known
  ids come from `GET /markets?id=A&id=B…&closed=true|false&limit=N`, batches of 50. The
  `closed` filter still applies, so each batch is requested for both states. Found when the
  first real fetch listed zero events; normalization merges both response kinds, newest first.
- A full month is large: 405,828 markets were scheduled to end in July 2026 (404,479 closed,
  1,349 still open on 2026-09-14). Listing them took about 20 minutes of sequential keyset
  pages. Of those, 254,390 were sports, 118,258 crypto and 19,602 weather.
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
- **Two row shapes carry the outcome.** UMA lifecycle rows have `price`. Native V2 and
  terminal CTF rows have **no `price`**, but carry `payouts` (micro-USDC per outcome, e.g.
  `[0, 1000000]`, or `[500000, 500000]` for 50/50) and an explicit `resolved_at`. In the July
  2026 sample, 81 of 600 markets (13.5%) used the `payouts` shape. The first normalizer read only
  `price`, so those outcomes came from Gamma alone, with no chain cross-check; this was caught in
  the data-quality review and fixed. Both shapes are now parsed, and a market is only settled
  when Gamma and the chain agree.
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

## M1 dataset: `july-2026-stratified-600` — accepted 2026-09-14

| | |
| --- | --- |
| Universe | 405,828 markets scheduled to end in July 2026 (404,479 closed, 1,349 open) |
| Sample | 600 markets, seed 20260914, 11 `feeType` strata (57–59 each; "other" has only 17) |
| Fetch | 4,402 + 530 requests, 0 retries, 0 failures; raw layer ≈ 170 MB compressed (excluding the 203 MB listing) |
| Normalized | 600 markets, 1,200 tokens, 41,720,927 bars (2025-05-02 → 2026-09-14), 370,209 prints |
| Rebuild | bit-identical, hash `80e1d97c…c324ee`, ≈ 9 min per rebuild |
| Full 1-minute history | 563 / 600 markets (bars from within 60 min of open to within 60 min of close, no gap > 60 min); 66 tokens have a gap > 60 min, 4 start late, 4 end early |
| Resolutions | 374 NO, 127 YES, 39 OUTCOME_0, 50 OUTCOME_1, 9 INVALID (50/50), 1 UNRESOLVED (still `posed`); 0 source disagreements |
| Unknown min order size | 4 markets |
| Liquidity reality | 99.46% of bars have no trade print in the minute; median staleness 834 min; 139 of 1,200 tokens never traded |
| Bars vs prints | 64.8% of bars with prints are within 1 tick of the minute's print range; 8.7% inside it |
| YES/NO on prints | median \|p_yes + p_no − 1\| = 0.01, p95 0.028 (bars: exactly 0 for 99%+) |

The liquidity numbers are the most important input for M2: most of these markets are thin, so
`TRADE_PRINT` execution will reject most signals as `UNEXECUTABLE`. That is the honest answer,
not a defect.

## M3 collection: `july-2026-liquid-sports-300` — added 2026-09-14

Why it exists: the built-in Underdog strategy found **0 signals** on the stratified sample,
because none of its 58 sports markets reached the strategy's $10k as-of volume filter. A
backtest needs markets where the strategy could act at all.

| | |
| --- | --- |
| Universe | Gamma sports markets (`feeType` sports) scheduled to end in July 2026 |
| Pre-filter | Gamma **final** lifetime volume ≥ $7,500 |
| Sample | 300 markets, seed 20260915 |
| Safe for | strategies with `universe.min_volume_asof` ≥ $10,000 |

**Why the pre-filter doesn't bias strategies with a high enough as-of volume filter.** A market
excluded here has final Gamma volume < $7,500. The engine's as-of volume is cumulative
*print notional* on the traded token up to the bar. On the stratified 600-market sample, of the
577 markets with any prints, 573 had Gamma lifetime volume ≥ their total print notional
(median ratio 1.55×) and the lowest was 0.797× (a mentions market, $3,399 Gamma vs $4,266
prints). With a 0.75 safety ratio, an excluded market's print notional is at most
7,500 / 0.75 = $10,000, so it could never pass a ≥ $10k as-of filter and could never have been
traded. The as-of filter counts one token and Gamma counts the whole market, which only widens
the margin.

This rests on an observed ratio, not a documented rule. The collection manifest records it
(`selection_uses_final_volume`), the API and simulator show it, and the engine attaches a
`SURVIVORSHIP BIAS` warning to any run below the safe level.

**First Underdog result (REALISTIC preset, $1,000):** 70 signals → 34 filled (31 no prints in the
execution bar, 5 below minimum size), gross +$3.73, fees −$11.68, slippage −$10.48, **net
−$18.43**. Costs are 5.9× the gross edge. Most exits are stop-losses on 1–30¢ outcomes, where one
tick is a large fraction of the price. This run is a baseline, not a tuned result: nothing should
be optimised on it before the out-of-sample and robustness tooling (M5).

**What the M4 explanations show about that run** (hypotheses for M5, not conclusions):

| Exit reason | Trades | Win rate | Net P&L |
| --- | --- | --- | --- |
| STOP_LOSS | 17 | 12% | −$38.42 |
| MAX_HOLD | 9 | 11% | −$2.74 |
| TAKE_PROFIT | 6 | 100% | +$31.02 |
| MARKET_RESOLVED | 2 | 0% | −$8.29 |

- *Ticks swamp sub-2¢ entries.* The worst trade (#43, −$12.93) bought a 1.6¢ outcome: VWAP 0.0162
  plus one tick, rounded up against the trader, filled at 0.018, a 12% cost on entry. The −20%
  stop sat a few ticks below and fired on noise, then exited in seven liquidity-capped pieces.
  `price ≤ 0.20` has no floor.
- *Exit rules fire on the sampled price, fills happen at print VWAP.* In trade #1 the sampled
  mid-like price ran about a tick below the prints the whole time, so the price the rules saw
  was not the price the exit got.
- *Near misses cluster.* The nearest rule failures are often several bars in the same few markets
  (Spain 3–3 Argentina appears 3 times in the top 15), each one clause a few percent short.
- *The built-in Underdog carries a `spread_proxy` exit rule*, which is always null on v1 data
  (PRD §4.2), so it can never fire. Checklists show it as "no data".

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

### Normalized layer is single-version — deferred to M6

`make rebuild-normalized` replaces the normalized tables in place. Each build records a
`dataset_versions` row with its content hash, but older versions' rows are not kept. A backtest
run that references an older `dataset_version_id` can detect the mismatch (`syntha-ingest
verify`) but can't be re-run on the old data without rebuilding from the same raw inputs. Decided
in M3: runs pin `dataset_version_id` and the hash in their manifest; versioned snapshots wait for
M6 (reproducibility). See docs/DECISIONS.md.

### Token ids stored as text on every bar — open (M2)

`clob_token_id` is a 77-digit decimal string repeated on every `price_bars` row, the dominant
storage cost. Consider an integer surrogate key or Timescale compression on `price_bars`
once the engine's read patterns are known.

### Unknown minimum order size — resolved (M1, migration 0004)

4 of the 600 sampled markets (small Trump-mentions politics markets) have `orderMinSize: null`
in Gamma, and `/clob-markets/{condition}` has no `mos` field for them either. Dropping them
would shrink the universe (PRD §5.1 rule 6), and assuming the common value of 5 would invent
data. `markets.min_order_size` is nullable, the report counts such markets
(`markets_without_min_order_size`), and **the engine must mark any order on them
`UNEXECUTABLE`** (M2).

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
| Closing a whole position is allowed below the market's minimum order size | Not documented; the minimum is applied to entries only | Low. Revisit if it matters (a sub-minimum position could otherwise never be sold) |
| TRADE_PRINT liquidity = all taker prints in the bar, both sides | PRD §11.3 "Σ print size in bar" | Medium: a BUY consumes ask liquidity, so taker-BUY prints alone would be stricter |
| Open positions at the end are worth their last mark, with no exit costs | Engine convention (`END_OF_BACKTEST`) | Stated in every run's data-quality assumptions |
