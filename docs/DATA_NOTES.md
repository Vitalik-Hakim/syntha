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

**2026-09-14 addendum — a poisoned cursor, not a rate limit.** Listing August 2026 through
`/markets/keyset` (about 460,000 markets, 4,600 pages) failed three times at page 4,612 with a
Cloudflare HTML 403. It was not throttling: the same request failed at 8 req/s and at 4 req/s and
from `curl` minutes later, while page 4,611's cursor, page 4,000's cursor and a fresh page 0 all
returned 200 at the same moment. One specific opaque `after_cursor` value is rejected by the WAF,
deterministically. Two changes: the listing resumes from the newest saved page of the same window
(`fetch.saved_listing`), and on a 403 for a cursor it re-fetches the previous page one item
shorter (`limit` 99, 98, …), which moves the page boundary and yields a different cursor
(`polymarket.list_markets`). Callers de-duplicate markets by `conditionId`.

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
- *The honesty layer's verdict on Underdog (M5):*

  | Preset | ROI | 90% CI | Trades | Beat permuted | Buy and hold |
  | --- | --- | --- | --- | --- | --- |
  | Realistic | −1.84% | −6.3% to +2.5% | 34 | 0% | +12.50% (90% CI −10.4% to +37.8%) |
  | Optimistic | −8.31% | −18.6% to +2.1% | 42 | 69% | −5.32% |
  | Conservative | −5.08% | −11.0% to +1.1% | 5 of 144 signals | 49% | +3.82% |

  Under Realistic, all 200 market-shuffled variants did better than the strategy. Its
  entry condition picked worse-than-random markets at the same moments; part of that is price
  level, since cheap outcomes cost more to trade. It also beat only 18% of random-entry
  baselines. The test is weak here: 31 of 70 signals had no other eligible sports market at that
  bar. Buy-and-hold's +12.5% looks strong but its interval spans −10% to +38%, and it came out
  −5.3% and +3.8% under the other two presets. No preset shows evidence of edge.
- *The built-in Underdog carries a `spread_proxy` exit rule*, which is always null on v1 data
  (PRD §4.2), so it can never fire. Checklists show it as "no data".

## M6 collections and strategy research — 2026-09-14

**Collections.** Both use the liquid pre-filter (final Gamma volume ≥ $7,500, so strategies need
`min_volume_asof` ≥ $10,000 to be survivorship-free) and are stratified by fee category (about 40 per
category).

| Collection | Role | Markets | Notes |
| --- | --- | --- | --- |
| `july-2026-liquid-400` | discovery | 400 | seed 20260916; sampled from the saved July listing |
| `june-2026-liquid-400` | holdout | 400 | seed 20260917; fresh Gamma listing; never used to design or tune anything |

The normalized layer now holds 1,670 markets and 124.7M price bars. A full rebuild takes 33
minutes.

**Method.**
1. Five candidates were written from known prediction-market effects before looking at outcomes
   (favourite–longshot bias on both sides, late drift, overreaction, momentum).
2. A calibration of price against outcome on July only (`tools/research/calibration.py`) added two
   longshot candidates.
3. All seven were screened on July under Realistic ($1,000, seed 1).
4. All seven were then run **unchanged** on June.

Every run is persisted and browsable in the app (BT-20260914-00015 to -00028).

**July calibration** (all volumes, both tokens): tokens priced 0.03–0.10 paid out 2–8 cents more
than their price at every offset from −72 h to +1 h, and 0.90–0.97 favourites correspondingly less.
That is the *reverse* of the classic favourite–longshot bias. The standard errors (±4–7 cents, about
30 markets per cell, correlated across offsets) are as large as the effect. Near-certain outcomes
(0.97+) in the last two hours earned +0.3 to +0.5 cents gross, less than one tick of slippage.

**Results** (Realistic, ROI with bootstrap 90% CI and permutation percentile):

| Strategy | July (discovery) | June (holdout) |
| --- | --- | --- |
| Momentum Breakout | **+5.89%** (−3.6..+15.8), 97% | **−6.66%** (−18.2..+5.1), 6% |
| Overreaction Fade | +2.54% (−2.2..+8.1), 6% | +1.01% (−4.8..+7.2), 66% |
| Favourite to Resolution (YES) | −3.17% (−13.2..+4.3), 82% | +3.03% (+1.9..+4.2), 100% |
| Late Favourite Drift | −2.96% (−21.9..+14.6), 68% | +9.22% (−3.0..+19.0), 54% |
| Longshot to Resolution (YES) | −3.80% (−6.3..−1.4), 16% | +4.61% (−0.8..+11.8), 95% |
| Favourite to Resolution (NO) | −3.39% (−13.4..+3.0), 40% | +0.04% (−3.4..+2.8), 86% |
| Longshot to Resolution (NO) | −2.39% (−5.9..+1.6), 7% | −5.98% (−8.5..−3.6), 1% |

**Reading.**
- **No candidate is robustly profitable.** July's best result, Momentum Breakout at +5.89% and
  the 97th percentile, is exactly the best-of-N illusion the honesty layer exists for: it lost
  6.66% on June.
- **Four of seven flip sign between months.** With 10–40 trades per run, one month of markets
  cannot separate an edge from the month's regime.
- **Side matters through base rates.** Buy-and-hold on the YES (first-outcome) side made −0.65% in
  July and +24.7% in June; on the NO side, −5.0% and −18.6%. YES-side strategies inherit that tide.
  The permutation test controls for it (same side, same moments); raw ROI does not.
- **Leads worth more data, not money:**
  - Overreaction Fade was positive in both months, but its intervals span zero.
  - Favourite to Resolution (YES) was clearly positive in June only.
- **Consistent loser:** buying NO longshots.
- **The longshot calibration did not survive execution:** buying a 5-cent outcome costs about
  20% in one tick.

**What it would take to find a real edge.** More independent months (each adds about 400 markets,
~30 minutes of fetching and a longer rebuild), then one pre-registered test pooled across months,
with the number of configurations tried reported alongside. Parameter sweeps belong to v2 (PRD
§18), with out-of-sample splits and multiple-comparison accounting.

## M7 strategy search — 2026-09-14

Protocol: docs/RESEARCH_PROTOCOL.md (written first). Data: five liquid 400-market months,
March–May 2026 for discovery, June–July for the holdout. Full tables: `data/research/*/table.md`.

**Stage 1 (pre-registered, 40 configurations on March–May, Realistic, $1,000): nothing met the
rule.** Expected best-of-40 by chance: +4.9% pooled ROI (95th percentile +10.3%).

| # | Configuration | Pooled ROI | 90% CI | Months + | Trades | Perm | Mar | Apr | May |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Longshot to Resolution 0.10–0.20 within 24h | +10.3% | −6.7% to +30.2% | 3/3 | 95 | 92% | +8.0% | +3.5% | +19.5% |
| 2 | Favourite to Resolution (YES) [M6] | +2.7% | −0.7% to +5.6% | 2/3 | 85 | 80% | +7.1% | +2.9% | −1.9% |
| 3 | Longshot to Resolution 0.03–0.10 within 24h | +1.2% | −14.0% to +23.4% | 1/3 | 84 | 4% | −11.4% | −13.0% | +28.2% |
| 4 | Favourite to Resolution ≥0.95 within 6h | +1.2% | −1.3% to +3.8% | 2/3 | 150 | 86% | +3.9% | +2.4% | −2.6% |
| 9 | Early Favourite ≥0.9 to Resolution | +0.6% | +0.5% to +0.8% | 3/3 | 30 | 73% | +0.3% | +0.6% | +1.0% |
| 15 | Momentum Breakout [M6] | −0.5% | −3.3% to +2.3% | 2/3 | 74 | 42% | +0.3% | −3.3% | +1.4% |
| 23 | Overreaction Fade [M6] | −2.6% | −6.0% to +0.8% | 1/3 | 48 | 12% | −2.8% | +0.1% | −4.9% |
| 34–40 | Late Drift to Resolution (all six) | −7.5% to −13.3% | all below zero at the top | 0/3 | 149–166 | 11–48% | | | |

Reading:
- The two M6 "leads" did not survive three more months: Overreaction Fade −2.6%, Momentum
  Breakout −0.5%.
- Every hold-to-resolution *favourite* configuration at 0.85 or 0.90 lost money; only ≥ 0.95
  scraped a positive pooled result with a CI spanning zero. Tokens at 0.85–0.95 were, on average,
  overpriced in these months.
- The mirror of that, cheap tokens priced 0.10–0.20 in the last day, is the one configuration
  positive in every month. Its P&L is lumpy (most longshots lose everything, a few pay 5–10×), so
  95 trades give a CI from −7% to +30%, and it sits exactly where the best of 40 lands by chance
  5% of the time. Suggestive, not evidence.
- *Late Drift* (buy the side that rose in the last day, hold) is a consistent loser in every
  setting and every month: late rises overshoot.
- VWAP reversion and the exit-based swing families are dominated by costs (cost ratios 1–2.5).
- Buy-and-hold on an `EITHER` universe (coin-flip side) was −13% pooled: buying at random loses
  the spread and the fees, which is the right null.

**Stage 2 (exploratory holdout, June + July; docs/RESEARCH_PROTOCOL.md):** the six nearest
misses ran once on the holdout months.

| Configuration | Jun | Jul | Pooled | 90% CI | Perm | Conservative |
| --- | --- | --- | --- | --- | --- | --- |
| Longshot to Resolution 0.10–0.20 within 24h | **+41.8%** | **+7.0%** | **+24.4%** | +4.0% to +48.0% | 98% | +15.5% (2/2) |
| Favourite to Resolution (YES) | +3.0% | −3.2% | −0.1% | −5.4% to +3.7% | 91% | −1.0% |
| Early Favourite ≥0.9 | +0.1% | −0.7% | −0.3% | −2.4% to +1.3% | 98% | −0.6% |
| Favourite ≥0.95 within 6h | −0.1% | −1.8% | −1.0% | −3.2% to +0.9% | 78% | −1.1% |
| Longshot 0.03–0.10 within 24h | +5.2% | −9.3% | −2.0% | −8.5% to +6.1% | 36% | +16.8% (1/2) |
| Longshot to Resolution (NO) | −6.0% | −2.4% | −4.2% | −6.3% to −1.9% | 4% | −6.7% |

The *Late Fade* mirror grid (buy the side that fell late), read off the discovery data, made
+12.9% in-sample and +3.7% / one negative month on the holdout, beaten by chance 42% of the time:
rejected. The lesson generalises: a rule derived from a loss on the same data is not a finding.

**The one survivor: late longshots.** Buy either outcome priced 0.10–0.20 within 24 hours of the
scheduled end, hold to settlement, 2% of starting capital per trade, at most 40 positions.
Positive in all five months (Mar +8.0%, Apr +3.5%, May +19.5%, Jun +41.8%, Jul +7.0%; ≈ +16%
per month pooled), and above its permuted and random variants every month. Why it is *not*
called an edge yet: it was the best of 40 on discovery (which alone is consistent with chance
at the 5% level), it reached the holdout through the exploratory branch, its P&L is a few large
payoffs against many total losses, and five months of 2026 cannot show persistence. Stage 3 of
the protocol (two untouched months) was the pre-registered next test, and it failed:
February −29.6%, August −4.0% (Realistic), both negative under Conservative too. Pooled over
the four out-of-sample months (Jun, Jul, Feb, Aug) it is +3.8% with a 90% CI of −9.9% to +19.0%.
**Not confirmed; the evidence now favours no edge.**

*Anatomy of the failure* (`data/research/anatomy-*.json`, February + August): 69 trades, 7
winners (10% paid out against a mean price of 0.16, below fair), net −$336. Thirty of the trades
were crypto longshots and all but one lost (−$461); in the five earlier months the same category
had a 38% payout rate and was profitable, while sports, a loser then, gained here. The category
split seen after stage 2 was noise, exactly as it was flagged. Costs were irrelevant to the
result (fees $19, slippage $18).

*Anatomy of the survivor* (146 trades over five months, `data/research/anatomy-*.json`):
five-month pooled ROI +16.0% per month, bootstrap 90% CI +2.3% to +30.5%, 2.5% of resamples lose.
26% of trades paid out against a mean entry of 0.19 (fair would be 19%), winners paid 5.2× on
average, fees and slippage were only $60 on $1,341 traded. But **five trades made $786 of the
$797**: without them the result is +$11, and only 2 of the 5 months stay positive without their
single best trade. Each is a different market and event, so they are independent wins, not one
bet, but a strategy whose result is five trades is a strategy whose next five months can
easily be flat. Two more caveats: 16 fills landed above 0.25 because the next bar's prints had
already jumped (the engine has no limit-price semantics; a real order would not have filled at
0.5–0.99) — without them the pooled ROI is +13.4% with a CI of +0.3% to +28.2%; and by category
the gain is entirely outside sports (90 non-sports trades +$935, 33% winners; 56 sports trades
−$137, 14% winners), a split noticed after the fact and therefore not something to act on.
Browsable runs: June
BT-20260914-00041 (Realistic), -00042 (Optimistic), -00043 (Conservative); July -00044, -00045,
-00046; February and August BT-20260914-00049 (Conservative), BT-20260914-00047 (Realistic), BT-20260914-00048 (Optimistic), BT-20260914-00050 (Realistic), BT-20260914-00052 (Conservative), BT-20260914-00051 (Optimistic).

---

## M8 — engine 0.7.0 reference re-run and PRD v3 probes — 2026-09-14

### E1/E2 re-run of BT-20260914-00041 (the reference for how much the two fixes change a known result)

BT-20260914-00041 is the M7 survivor (*Longshot to Resolution 0.1–0.2 within 24h*) on
`june-2026-liquid-400`, Realistic preset, $1,000, seed 1, engine 0.6.0: +41.8%, 21 trades. The
same request was re-run under engine 0.7.0 in four execution variants (parent run 00041; all
browsable). `order` and `liquidity_side` are the only differences between the four.

| Variant | Run | ROI | Trades | Win rate | Signals → filled / refused | Traded notional | Perm. pct | ROI 90% CI (trade) | Cluster 90% CI (key) | Return/$ (CI) | MDE@80% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v2 semantics: MARKET, BOTH | BT-20260914-00060 | **+41.8%** | 21 | 38% | 54 → 21 / 32 NO_PRINTS | $221 | 98 | +5.8% to +83.8% | +5.2% to +84.1% (event) | 1.89 (+0.26 to +3.53) | 2.45 |
| E2 only: MARKET, SAME_SIDE | BT-20260914-00058 | +22.7% | 20 | 40% | 54 → 20 / 33 NO_PRINTS | $187 | 86 | −0.7% to +49.0% | −1.2% to +49.8% (day) | 1.22 (−0.08 to +2.41) | 1.85 |
| E1 only: LIMIT, BOTH | BT-20260914-00059 | +27.8% | 21 | 19% | 47 → 21 / 22 LIMIT_EXPIRED | $132 | 88 | −5.4% to +71.0% | −5.7% to +71.7% (event) | 2.11 (−0.52 to +4.82) | 4.03 |
| **E1+E2: LIMIT, SAME_SIDE (v3 default)** | **BT-20260914-00057** | **+23.6%** | 19 | 21% | 65 → 19 / 43 LIMIT_EXPIRED | $125 | 79 | −8.8% to +66.4% | −8.7% to +66.6% (day) | 1.90 (−0.90 to +4.77) | 4.28 |

Reading:
- The v2-semantics variant reproduces 00041 exactly (ROI, trades, signals, refusals): the E1/E2
  code paths are opt-in and the old path is intact.
- **The two fixes take the known result from "consistent with an edge on this slice" to "not
  distinguishable from luck" without touching the strategy**: +41.8% (CI +5.8% to +83.8%,
  permutation 98) becomes +23.6% (CI −8.8% to +66.4%, permutation 79). This is the correction
  to expect on every v2-era hold-to-resolution number, and it is why v3 studies run under E1/E2
  only.
- E1 (limit orders) is the larger change in *kind*: the win rate falls from 38% to 19–21%. A
  limit at the signal price + 1 tick fills when other takers are buying at or below it, which
  is disproportionately in markets drifting down; the chased fills that filled 0.3–0.5 above
  the signal in v2 (16 of the survivor's 146 fills in M7's anatomy) are now
  `UNEXECUTABLE_LIMIT_EXPIRED` (43 of 65 signals under SAME_SIDE). Expired orders end the
  attempt and the episode re-signals at later candidate bars, which is why the signal count
  rises from 54 to 65.
- E2 (same-side liquidity) mostly changes *size*: traded notional falls from $221 to $187 with
  the same signals; with 20 trades the ROI difference (+41.8% → +22.7%) is a couple of fills.
- Cluster intervals are barely wider than trade-level ones here because the 19–21 trades are
  already one per event and mostly one per day. Return per dollar risked is 1.2–2.1 with
  intervals spanning zero; the MDE@80% of 1.9–4.3 per dollar says this sample could not have
  detected anything smaller than "the position roughly triples" — longshot P&L is that lumpy.
- Fees: HISTORICAL and CURRENT coincide for these markets (no schedule change applies), so the
  fee counterfactual is a no-op on this run; it is not on category fee changes elsewhere.
- Capacity (E5): under the v3 default, $45 per month capturable at $1,000 starting capital and
  $56 at $10,000, with 94% / 98% of signals size-constrained by the 10% participation cap on
  same-side prints in the fill bar. The "month" is the signal span (85 days for this collection,
  because the June markets' scheduled ends spread over three months), not the equity-curve span.
  Cross-check: the 19 filled trades traded $124.58; Σ min(requested, available) over all 65
  signals is $125.13.

Raw numbers: `data/research/m8-rerun-00041.json`.

### Gamma and websocket probes for PRD v3 (Appendix B, partial: what M8 uses)

- `GET /markets` rows carry `negRisk`, `feeType`, `volume24hr`, `acceptingOrders`, `closed`,
  `enableOrderBook`, `clobTokenIds` (JSON string) and `events[0].{id, negRisk, ticker}`.
  `?condition_ids=A&condition_ids=B` (repeated parameter) returns exactly those markets (2 rows
  for 2 ids); `offset` pages the `order=volume24hr` listing (offset 900 → 100 rows at
  $4.1k–4.8k of 24h volume). Fee-type stems seen at ranks 900–1,000: sports 35, weather 22,
  crypto 15, none 7, culture 6, politics 5, finance_prices 5, tech 3, economics 2.
- Websocket `last_trade_price` (live capture): `{asset_id, event_type, fee_rate_bps, market,
  price, side, size, timestamp (ms), transaction_hash}`; the captured print was a BUY of 5 at
  0.002 with `fee_rate_bps: "0"`. A `best_bid_ask` event (`best_bid`, `best_ask`, `spread`)
  also arrives; it is not stored. The recorder now writes `last_trade_price` as
  `orderbook_deltas` rows with `event_type = 'trade'` and `fee_rate_bps`.
- Not yet probed (needed from M9): `prices-history` at `fidelity=60` for 2025 markets, Data API
  `/v2/trades` paging depth and a `user` filter, `/activity` and `/positions`, `/v2/resolutions`
  row shapes.

### Wallets, catch-alls and neg-risk in the normalized layer (migration 0010)

- `trade_prints.proxy_wallet` is new and **NULL for every existing row**; the raw `/v2/trades`
  records carry `proxy_wallet` (M1 finding), so the next `make rebuild-normalized` (≈ 41 min for
  the seven months) fills it. Until then wallet-triggered strategies (E4) see no wallets on real
  data; the golden fixture is the E4 acceptance. Wallet addresses are pseudonymous public keys;
  they stay inside the app and research artefacts and are never published.
- The dataset content hash covers `SELECT *`, so `make verify-normalized` reports a mismatch
  against the pre-0010 dataset version until that rebuild. Expected, not corruption.
- `markets.neg_risk` now reaches the engine (`MarketTerms.neg_risk`). Baskets (E3) only ever
  form on events whose listed markets are all neg-risk; anything else is not a mutually
  exclusive set and the pre-screen skips it.
- Catch-all detection (v3 §4.2) is an M8 text rule on the question of a neg-risk market:
  a leading `other | another | someone else | anyone else | none of the above | no one | nobody`,
  or `will (another | any other | some other | someone else | anyone else | no one | nobody)`,
  or `none of the above` / `any other (candidate | team | player | person | country | outcome)`
  anywhere; case-insensitive (`syntha_worker.loader.CATCH_ALL`). Conservative on purpose: a
  false "catch-all present" would let a basket treat a non-exhaustive set as exhaustive. D2
  (M11) replaces it with event-level parsing; `EventView.catch_all_present` is the only consumer.

## M9 prep — PRD v3 Appendix B probes and the three decisions — 2026-09-15

### Appendix B probes (live, 2026-09-15)

- **Gamma listing per month.** `GET /markets?closed=true&end_date_min=2025-07-01T00:00:00Z&
  end_date_max=2025-07-31T23:59:59Z` works for 2025 (offset and keyset paging both; `order=
  volumeNum` for probes only). A keyset page of the July-2025 frame: 100 markets, **99 with
  `feeType: null`** (the fee-free era) and 59 `negRisk: true`; the embedded `events[0]` object
  carries `id`, `slug`, `ticker`, `title`, dates, `negRiskAugmented` and volumes, but **no
  `tags` and no market list**. `GET /events/{id}` returns `tags` (slugs and labels) and the
  event's `markets`. Consequence: category for 2025 markets comes from event tags, so the census
  sampler fetches every event of the frame (one call each) before it allocates; structure
  (`sibling` = event with ≥ 3 markets) uses the same object.
- **`negRiskOther`** is a Gamma market field (True for 1 of the 100 listed July-2025 markets;
  False for all five Fed-decision markets of 2026, an event without a residual market). It is
  Polymarket's own flag for the neg-risk "Other" market and is stored as `markets.neg_risk_other`
  at the next rebuild. It makes a market a catch-all *candidate*; the M9 decision below requires
  a review before the engine treats it as one.
- **`/markets/{id}` batches and `closed`:** unchanged from M1 (`?id=A&id=B…`, both states);
  `?condition_ids=A&condition_ids=B` (M8) also works for 2025 markets.
- **CLOB `prices-history` for a 2025 market (June 2025, checked at July's first listed
  market):** `fidelity=60` over 15 days → 360 hourly points (200); 16 and 30 days → `"'startTs'
  and 'endTs' interval is too long"`; `fidelity=1` over 1 day → 1,440 points and over 15 days →
  21,598 points, so **1-minute history is still served for 2025**; `interval=max&fidelity=60` →
  empty. The census uses `[end − 15 d, end]` at fidelity 60 and `[end − 6 h, end]` at fidelity 1
  for intraday and ultra-short markets.
- **Data `/v2/trades?condition=`:** the busiest July-2025 market (Fed +25 bps, $43.5M) returned
  12 pages × 1,000 prints in 9.8 s with `has_more` still true; page 12's earliest print was
  2025-07-14 for a market ending 2025-07-30, i.e. 16 days of prints in 12 pages on the busiest
  market of the month. `pagination` = `{limit, offset, has_more, next_cursor}`. The census walks
  newest-first until the page's earliest print is older than `end − 8 d` or 10 pages, and marks
  the market `prints_truncated` when the cap hit first (liquidity tiers then stay null where
  the window is uncovered).
- **`/v2/trades?user=`** exists: 35 rows for one wallet across several markets, and combined
  with `condition=` it filters both ways. **D3 (M10) can use v2 directly**; the v1 fallbacks also
  answered: `GET /activity?user=&type=TRADE` (camelCase rows with `usdcSize`, `transactionHash`,
  `proxyWallet`), `GET /positions?user=` (rows with `avgPrice`, `curPrice`, `cashPnl`,
  `realizedPnl`, `percentPnl`, `redeemable`, `mergeable`, `negativeRisk`, `oppositeAsset`) and
  v1 `GET /trades?user=` (camelCase, 10,000-offset cap per M1).
- **`/v2/resolutions`** for 20 July-2025 markets: 20 rows, all in the UMA `price` shape with
  `last_update_timestamp` as an RFC3339 string; no `payouts` rows in this batch. Both shapes are
  still parsed (M1 normalizer).
- **Websocket** `last_trade_price` with `fee_rate_bps`: verified in M8 (above).
- **Kalshi:** not probed (S6 is optional and starts at M13 at the earliest).

### Census (`syntha-ingest census-*`, PRD v3 §4.1)

- Metadata is taken from the listing pages themselves (they carry the full market object with
  `events[0].id`); the per-id refetch of M1 is not repeated. Events are fetched once per frame
  for tags and sibling counts.
- Offsets: `−7d −3d −24h −6h −1h −15m` from the scheduled end, `+1h +24h` from the open,
  skipped outside the market's life (open … min(closedTime, end)).
- `fill_10m` / `fill_30m`: VWAP of taker-BUY prints on the token in `[ts, ts + window)` at
  ≤ `mid + 1 tick`; null when none, or when the print walk did not cover `ts`.
  `liq_24h`: taker-BUY notional in `[ts − 24h, ts]`, null unless the walk covered the window.
  `staleness_minutes`: minutes since the last print at or before `ts` (null when unknown).
- `fee_entry`: HISTORICAL schedule via the engine's fee module (2025 → 0);
  `fee_entry_current`: the fee a 1-share fill would pay **today** in the market's category
  (the category's latest schedule from the fee seed; categories without a schedule pay 0). This
  is the CURRENT-fee counterfactual v3 §4.6 asks for on fee-free months.
- `payout` is written from the resolution path (Gamma/chain agreement rule) after the feature
  columns, and is tagged engine-restricted like `markets.resolution`.
- Determinism: the build reads raw records in (fetched_at, page) order, later fetches win,
  rows are written in (condition, token, offset) order, and `census_runs.content_hash` covers
  `census_markets` and `census_observations` for the census id; `census-verify` recomputes it.
- Census price records share the CLOB endpoint directory with 1-minute bar fetches; they carry
  `fidelity` in their unit and `bar_lines` ignores anything but fidelity 1.

### Census 2025-07 — first month, acceptance passed (2026-09-15)

`make census-sample MONTH=2025-07 SEED=20250701` then `make census-fetch`, both at 20 rps,
concurrency 16. Manifest: `data/raw/_census/census-2025-07.json`.

- **Frame:** 8,730 closed markets scheduled to end in July 2025 (88 keyset pages, about a
  minute), 4,811 events fetched for tags and sibling counts (4 minutes). Nothing was
  unobservable (every listed market had tokens and an open time).
- **Allocation:** 6,147 markets over 25 (category × duration) strata. The 2,000 cap bound in
  two strata only: `crypto/multi_day` (3,664 available) and `sports/multi_day` (2,919). July
  2025 had almost no `ultra_short` markets (95: sports 88, politics 6, tech 1), so the 500 cap
  never bound. Categories that month, from event tags (no market had a `feeType`): sports
  2,621, crypto 2,039, weather 448, `other` (untagged) 456, politics 425, geopolitics 111, tech
  43, finance 4; no `culture`, `economics` or `mentions` tags. 3,431 markets (56%) belong to an
  event with ≥ 3 markets (`sibling`); 2,878 are neg-risk.
- **Fetch:** 308 resolution batches (20 s), 6,147 trade walks = 6,813 pages (5.6 min; 197
  walks stopped at the 8-day depth, 1 hit the 10-page cap → `prints_truncated`), 13,054 price
  windows (12,294 hourly + 760 one-minute windows for intraday/ultra-short markets; 492,158
  points on the resumed part alone). About 25,100 requests in total, ≈ 25 minutes of fetching
  — a quarter of the PRD's 62,000-request estimate, because the July-2025 frame is 8,730
  markets, not 20,000.
- **Resumability, exercised for real:** the machine's DNS resolver failed for several minutes
  mid-way through the price phase (`nodename nor servname provided`); 405 windows exhausted
  their six retries and landed in `data/checkpoints/census-2025-07.failures.jsonl`. The fetch
  was stopped, DNS came back, `census-fetch` was re-run: 6,455 units skipped as done, 4,585
  fetched (the 405 failures among them), 0 failed, 1 retry.
- **Build:** 39 s; `census_markets` 6,147 rows, `census_observations` 80,662 rows (57 MB).
  Two consecutive builds and `census-verify` give the same hash,
  `256249277051dbb79d38a4026b03006e6ea5031279e2c503ea0ef7dc6c1085d8`. All 6,147 markets
  resolved (NO 3,043, YES 608, `OUTCOME_0` 1,301 and `OUTCOME_1` 1,191 for markets whose
  outcomes are not Yes/No, INVALID 4); no Gamma/chain disagreements; no market dropped.
- **Coverage (the numbers that decide S1's power):**

| Offset | Observations | Has mid | `fill_10m` | `fill_30m` | Median staleness (min) |
| --- | --- | --- | --- | --- | --- |
| −7d (long markets only) | 3,282 | 88.7% | 13.3% | 18.8% | 271 |
| −3d | 7,136 | 94.3% | 1.1% | 1.8% | 1,512 |
| −24h | 11,266 | 95.7% | 1.5% | 2.9% | 740 |
| −6h | 11,600 | 96.6% | 2.0% | 3.7% | 352 |
| −1h | 12,104 | 96.9% | 22.8% | 27.0% | 94 |
| −15m | 12,156 | 97.1% | 15.7% | 18.5% | 68 |
| +1h | 12,104 | 71.7% | 0.5% | 1.3% | 12 |
| +24h | 11,014 | 84.3% | 1.2% | 2.6% | 88 |

  5,391 of 6,147 markets (87.7%) have a mid at −24h, above the 80% floor in the S1
  registration. **Fill rates are the story:** outside the last hour, fewer than 3% of
  token-observations saw a taker buy at ≤ mid + 1 tick within 10 minutes. July-2025 markets
  were thin; the calibration surface will be measurable on mids almost everywhere but
  executable only near the end (−1h: 22.8%, −15m: 15.7%) and, for long markets, a week out
  (13.3%). The S1 selection rule's fill-rate floor (30%) will bind hard in this month. Liquidity
  windows are covered for ≥ 96% of observations (null only where the walk stopped short).
- **Sanity:** no observation has `fill_10m` above `mid + tick`; `fee_entry` is 0 everywhere
  (fee-free era) and `fee_entry_current` is positive for 97% of fills (max 0.0175 = 7% × 0.25,
  crypto at 0.50), zero for the fee-free categories.
- **Deadline types (D4 first pass):** BY 111, ON 2,457 (mostly sports fixtures), NONE 3,579.
  S2's BY arm will be small in 2025 months; the registration's n ≥ 500 per arm is a real bar.
- **Raw layer:** ≈ 270 MB gzipped for the month (trades 188 MB, prices 59 MB, events 22 MB).

### Census months August 2025 → May 2026, C1 and January 2026 (driver, 2026-09-15)

Run by `tools/research/census_months.sh` in the order 2025-08 … 2025-12, 2026-02 … 2026-05
(discovery), 2026-06 … 2026-08 (C1), 2026-01 (C2); 2026-09 and 2026-10 wait until their
markets resolve. Per-month outcomes are appended below as the driver reports them.

| Month | Finished (UTC) | Markets | Observations | With mid | With fill_10m | Fill rate | Hash | Rebuild |
|---|---|---|---|---|---|---|---|---|
| 2025-08 | 2026-09-15 08:11 | 7,245 | 89,044 | 94.2% | 6,001 | 6.7% | `f5dff80fd8ba` | bit-identical, verified |
| 2025-09 | 2026-09-15 08:47 | 10,968 | 127,490 | 89.8% | 5,512 | 4.3% | `413141c4d4bb` | bit-identical, verified |
| 2025-10 | 2026-09-15 09:38 | 12,878 | 141,896 | 91.4% | 9,749 | 6.9% | `c72def590e05` | bit-identical, verified |
| 2025-11 | 2026-09-15 10:35 | 13,384 | 148,256 | 91.1% | 12,773 | 8.6% | `cc011ce861da` | bit-identical, verified |
| 2025-12 | 2026-09-15 13:13 | 19,126 | 234,086 | 70.5% | 9,194 | 3.9% | `79cc09bea79a` | bit-identical, verified |
| 2026-02 | 2026-09-15 14:30 | 20,002 | 243,384 | 86.2% | 13,294 | 5.5% | `019115bf2884` | bit-identical, verified |
| 2026-03 | 2026-09-15 15:35 | 21,413 | 256,390 | 86.7% | 12,603 | 4.9% | `e9201830245e` | bit-identical, verified |
| 2026-04 | 2026-09-15 16:32 | 21,395 | 257,486 | 81.2% | 10,225 | 4.0% | `6595ae742019` | bit-identical, verified |
| 2026-05 | 2026-09-15 17:30 | 22,916 | 272,708 | 82.7% | 9,131 | 3.3% | `b22eb02cc9c3` | bit-identical, verified |
| 2026-06 (C1) | 2026-09-15 19:38 | 23,367 | 287,884 | 84.5% | 9,679 | 3.4% | `0a39b344775b` | bit-identical, verified (fetch attempt 1 left failures; attempt 2 completed) |
| 2026-07 (C1) | 2026-09-15 20:51 | 22,532 | 278,012 | 86.6% | 5,059 | 1.8% | `950512ff156e` | bit-identical, verified |
| 2026-08 (C1) | 2026-09-15 23:08 | 23,369 | 285,310 | 86.0% | 4,075 | 1.4% | `7ccaf03669ec` | bit-identical, verified |
| 2026-01 (C2) | 2026-09-16 00:37 | 18,060 | 230,878 | 82.7% | 7,056 | 3.1% | `ec9c93944039` | bit-identical, verified |

All fourteen months (D: ten, C1: three, C2: 2026-01) are built, bit-identical on a second
build and verified. The fill rate falls through 2026 (6.7% in 2025-08, 1.4% in 2026-08): the
trade walk stops at end − 8 days or ten pages, and in the busy 2026 months ten pages of a
market's newest prints do not always reach the −7d/−3d offsets, so those offsets have no fill
(recorded as "no fill", never invented). The census sees the truncation; `fill_10m` at −24h
and later is unaffected.

### Full normalized rebuild with all fourteen census months (2026-09-16)

Version `8cac86a6…`, content hash `49627933a689…`, built by `make rebuild-normalized`
(`syntha-ingest normalize`) on the third attempt of the day: 3,669 recorder markets + 238,704
census markets, 484,746 tokens, 293,240,050 hourly/minute bars, 76,570,279 prints; the 14
`census-<month>` collections exist. Timings on the 16 GB laptop: parse and spool 24 min,
COPY of bars and prints with secondary indexes dropped and recreated 40 min, content
hash plus the data-quality report (now single-threaded, in a savepoint) 55 min — 120 min
in all. The quality report completed this time. Disk: the load consumed about 120 GB inside
the Docker VM; the two failed attempts before it are described in docs/DECISIONS.md ("Disk
incident") — the second filled the host disk and left 126 GiB of orphaned PostgreSQL files,
removed by hand after the user's approval, and the old layer was truncated before this attempt.
Runs created between 05:45 and 11:02 UTC on 2026-09-16 (none survive; the chain's junk runs
BT-20260916-00080…00084 were deleted) would have seen a partial layer.

### M9 discovery, first pass — a candidate that was an artefact (2026-09-15)

The pre-registered discovery command on the ten D months (1,851,402 observations, 660 primary
cells, cluster bootstrap by event and by day, BH at 10% FDR with N = 660) selected one cell:
**crypto, fill price [0.35, 0.50), offset −15m** — n = 15,921, fill rate 66%, E[r] = +0.283
(90% CI +0.267 to +0.298, day clustering), positive in all ten months, capacity $20k/month at
$1,000. Two cells passed BH but not the sign rule (crypto [0.35,0.50) −1h, crypto [0.50,0.65)
−15m). The same cell's **mid calibration was −0.7%**: at the price level those tokens were
fairly priced. Diagnostics on the discovery set, before anything else was run:

- The markets are 5- and 15-minute "Bitcoin/Ethereum/XRP Up or Down — March 28, 4:15AM–4:30AM
  ET" windows. They are *listed* one to three days ahead, so the §4.1 duration class (scheduled
  end − open) called them `multi_day` or `intraday` and they entered the primary cells — the
  "latency game" the registration excludes as `ultra_short`. They also consumed the
  `crypto/multi_day` cap (2,000) in every 2026 month.
- Among observations whose **mid** is in [0.35, 0.50) at −15m: those with a qualifying fill in
  the next 10 minutes won 38.1% (n = 11,570), those without won 66.5% (n = 5,840). Among
  observations whose **fill** is in the bucket (mean mid 0.501, mean fill 0.442, fill − mid =
  −5.8¢): 58.7% won. In 3,378 markets *both* tokens "filled" in the bucket — at 0.44 each, an
  impossible pair that mechanically returns +13%. The window VWAP of prints at ≤ mid + 1 tick
  is an average over a market's *dips* during the window; in a 15-minute crypto market the
  window is the whole life of the outcome. At −1h the same mechanics inflate crypto returns by
  about nine points (fill − mid = −5.4¢).
- So E2's "same-side prints as evidence of an ask" is sound for a resting market but selects
  dips wherever the price moves faster than the window. The registered estimand (`fill_10m`)
  keeps its definition; the build now also stores `fill_first_10m` (the earliest qualifying
  print, the first evidence that an ask ≤ limit existed) and the report shows E[r] on both.
  Where they disagree, the window VWAP is the optimistic one.

Consequences (reality wins; the registration's hypotheses are unchanged): `duration_class`
also reads the question's own window (`4:15AM-4:30AM`, "next 5 minutes"), applied at build time
for every month; the census tables were rebuilt (migration 0012 adds `fill_first_10m`) and the
discovery command re-run. The first-pass report is superseded; its numbers stay here as the
record of what the naive definitions produce.

Second pass (2026-09-15 19:36): with 22,174 ultra-short markets excluded, three crypto cells
still cleared the rule ([0.35,0.50) at −15m: +40%; [0.35,0.50) at −1h: +4.2% but −0.4% at the
first print and mid calibration −6%; [0.50,0.65) at −1h: +20%). Their markets were the 2025
*hourly* "Bitcoin Up or Down - August 5, 12PM ET" series, which states one clock time rather
than a range and had slipped past the window parser; a single clock time in an "Up or Down"
question now counts as a 60-minute window (`_SINGLE_CLOCK`). Third pass below.

**Third pass (2026-09-15 20:13, final for D):** with both series excluded — 29,319 ultra-short
markets, 279,100 observations, 59,840 of the 84,000 executable fills — **no primary cell passes
BH at 10% FDR**; candidates: none. Only 3 of 660 cells reach n ≥ 500 and 9 reach n ≥ 300, all
sports; every one has a 90% cluster interval spanning zero (E[r] from −21% to +2.7%; fill rates
2–10%; MDE@80% 8–22¢ per dollar). Fill rates on the non-ultra-short frame: −7d 2.9%, −3d 1.2%,
−24h 2.2%, −6h 2.8%, −1h 7.5%, −15m 14.1%. S2 test 1 has no contrast cell with 100 BY-arm
observations: underpowered on 2025–26 data. Report: `research/studies/s1_calibration/report/`.

### S2 deadline decay — rule rejected through the engine (2026-09-16)

`tools/research/confirm.py run` executed the registered S2 rule (R-0768) on the census
collections at 60-minute bars in the registered order D → C1 → C2, deciding preset REALISTIC
execution + CURRENT fees, CONSERVATIVE as the robustness arm; 28 runs BT-20260916-00085…00112.
D: 27 trades, +0.039 per dollar, 90% CI −0.135 to +0.189 (event clusters), MDE 0.246, positive
in 6 of 10 months, capacity $34/month — fail. C1: 9 trades, +0.296, CI −0.195 to +0.727, fail.
C2 (2026-01): 4 trades, −0.414, fail. Conservative C1 ∪ C2 negative. Verdict **rejected**
(`research/studies/s2_deadline_decay/report/verdicts.md`). The rule fires a few times a month
because BY-type markets with a NO price in 0.50–0.90 at 60–72 hours out and a flat week are
rare; the MDE (25 points on D) is far above the 5-point target, so this is a negative result
with an MDE, not evidence about the hypothesis. Test 1 (BY-vs-ON calibration contrasts) had
no BY arm with 100 observations and stays untested.

### Recorder growth per day — measured, not assumed (2026-09-16)

`make recorder-coverage` on 2026-09-16 (after the day's Docker restarts): last 7 days = 10
token-days, all on 2026-09-14 (2,620 rows, 0 trades, 0 gaps > 5 min, 0.0 raw MB); recorder
hypertables 0.00 GB; the active watchlist is empty ("run the recorder once to fill it"). The
recorder is not running and has recorded nothing since 2026-09-14; the README's M1 figure of
5–6M delta rows per day for 10 markets does not describe the present. S5 (maker simulation)
needs ≥ 30 days of E7 recording and has none yet.

### S1b — maker side of the census on D: family flat, five rule-passers, one without a tail (2026-09-16)

Registered before the run (R-0769, 176 cells) with the taker census on D recorded as the
motivation. Ten D months: 10,323,298 taker-BUY prints in the fill windows, 97% of them on
ultra_short markets (the 5/15/60-minute crypto series carry the taker flow); 61,131 in the
primary family (p < 0.35, offsets ≤ 24h). Pooled maker return +0.010 equal-weight, +0.000
size-weighted, adverse selection ≈ −0.006: the S1 taker's −10.7% in the same cells is mostly
the CURRENT fee and does not become a maker gain. Five cells pass the registered rule; one
(politics [0.01,0.03) −1h, 563 prints, no token ever won) has no loss event and a degenerate
interval; three rest on 6–12 losing events; one (politics [0.20,0.35) −6h, 674 prints, 100
events, 23 losing events, +0.229, CI +0.12 to +0.31, 9/10 months, $133/month) is a real
candidate. The report was given `wins (events)` and a "no tail observed" flag after the
first pass — reporting added, the selection rule untouched. C1/C2 not run (stopped after D
as instructed). Method notes: 2025 census tokens have no 1-minute bars (marks from the next
print); Docker's 64 MB /dev/shm forbids parallel workers on the 76M-print joins; the fills
query needs the month's time bounds for chunk exclusion (a full scan took > 45 min per
month); per-month fills are cached under data/checkpoints/s1b/.

### Recorder restarted with the final-24h slot, retention and a disk cap (2026-09-16 17:20 UTC)

Started detached (`data/recorder.log`) with `--final-24h-slots 6 --retention-days 30
--disk-cap-gb 20`. First refresh: 21 markets / 42 tokens, no `politics/final_24h` entry. Live probe the same
hour: of the 1,000 open markets whose `endDate` falls in the next 24 hours (Gamma
`end_date_min/max` on the open listing works, 10 pages), none carries `feeType` politics and
the embedded events carry no tags; the only politics-like ones are two `no_fee` geopolitics
day-markets. Politics markets in their final day are sparse (S1b's candidate cell had 138
markets in ten months); the slot fills when one appears. The refresh now also scans the
end-date-filtered listing (three pages) so a quiet politics market ending tomorrow is not
missed by the volume-ordered top 1,000. Thin strata stay empty for the documented reason (the
top-1,000 listing bottoms out near $4k/day). Growth per day is to be read from
`make recorder-coverage` after 24 hours (from 2026-09-17 17:20 UTC). Early read at 40
minutes (not the confirmation): 294,181 rows, 3,523 trades, 14.6 MB of raw frames for 42
tokens, hypertables 0.15 GB before compression.

### S1b confirmation on C1 and C2 (2026-01): one rejected, four underpowered, all provisional (2026-09-16)

Confirmation criteria written as a dated amendment after the D run and registered (R-0946);
all five D rule-passers run on C1 (2026-06…08) then C2 (2026-01), realistic (fill at the
print price) and conservative (one tick worse), losing-event counts per set. Verdicts:
crypto [0.03,0.10) −15m rejected (C1 pass on 1,689 prints / 16 losing events, C2 fail on 832 /
5); the other four underpowered (27–198 prints per set, 0–5 losing events). Provisional until
2026-09 joins C2. Report additions: event-weighted return, 2025 H2 / 2026 and BY / ON / NONE
splits on every cell (research/studies/s1b_resting_order/README.md). Method: the 2026 census
months hold up to 65M 1-minute bars each, so the marks now fetch only the bars at the fills'
mark minutes through a temp table (the first C1 attempt stalled at 27 GB of swap), and the
bars join hashes the fills' side.

### The three M9 decisions (product owner, 2026-09-15)

- **Capacity denominator = the collection's coverage window** (`Dataset.coverage`, from the
  collection's `end_date_min/max`, narrowed by an explicit request window). The M8 signal-span
  denominator remains the fallback only for datasets without a window (fixtures), and the
  stored metric names which one was used.
- **Baskets are refused on final-volume-filtered collections.** `create_run` (API 422) and
  `prepare` (ValueError) both refuse a basket strategy whose collection manifest carries
  `selection_uses_final_volume`: such a collection is missing the siblings that stayed thin, so
  no set identity can be checked. Ordinary strategies keep the existing bias warning.
- **Catch-all requires review.** The loader no longer infers `is_catch_all`; it reads
  `catch_all_reviews` (durable across rebuilds) and passes `None` otherwise, so
  `EventView.catch_all_present` is false until a person has reviewed the market.
  `tools/research/catch_all_review.py list` shows candidates (`negRiskOther` or the text rule),
  `set` records a verdict, `export`/`import` keep `docs/research/catch_all_reviews.json` in the
  repo.

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
