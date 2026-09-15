# Polymarket Strategy Lab — PRD v3: Edge Search Programme (M8–M13)

**Status:** build-ready spec
**Audience:** Claude Code and human reviewers
**Builds on:** PRD v2 (M0–M7, shipped), `docs/DATA_NOTES.md`, `docs/DECISIONS.md`,
`docs/RESEARCH_PROTOCOL.md`. Nothing in v2 is withdrawn; this document adds to it.
**Codebase names used here:** `syntha_engine`, `syntha_storage`, `syntha_contracts`,
`syntha_ingestion`, `tools/research/*`, tables `bt_trades`, `bt_equity`, `trade_prints`,
`price_bars`, `orderbook_snapshots`, `orderbook_deltas`.

---

## 0. How to use this document

Read §1–§3 before writing code. §1 says what the programme is for and what result to expect;
§2 says why the M7 search was narrow; §3 is the engine work that must land before any new
measurement, because every study below would otherwise measure a known distortion.

**Rules for the implementing agent (v2 rules still apply; these are added):**

1. **Every study is pre-registered before it runs.** A study's `registration.yaml` (§7.6) is
   committed, with the decision rule and the list of data already seen, before its discovery
   command executes. A study without a committed registration is not run.
2. **Every hypothesis tried is a row in the register** (`docs/research/REGISTER.md`), written
   by the tooling, not by hand. The best-of-N null uses N from the register.
3. **Negative results carry a magnitude.** "No edge found" is reported as "no edge larger
   than X per trade at 80% power" (§7.3). A negative without an MDE is incomplete.
4. **Point-in-time applies to people, sets and links, not only prices.** A wallet's record, an
   event's sibling set, and a nested-deadline pair are all computed as of the evaluation
   timestamp (§4.3, §4.2, §5.4).
5. **No live trading, no live copying, no order placement.** The programme ends at a paper
   scanner (§10). That boundary is not moved by a good result.
6. When reality differs from this document, reality wins; record it in `docs/DATA_NOTES.md`.

---

## 1. Purpose and the result to expect

### 1.1 What the programme answers

> Is there any edge that a small, rule-following account can capture on Polymarket — and if
> so, where, how large, and with how much capital?

v2 answered a narrower question ("do directional rules on 1-minute mids of liquid markets
have an edge?") and the answer was no. v3 widens the hypothesis space to the four places where
edges are documented or structurally possible, and fixes the sample-size problem that made the
v2 search unable to confirm an edge even where one exists.

### 1.2 The four hypothesis classes

| Class | Mechanism | Why it might survive where directional rules didn't | Study |
| --- | --- | --- | --- |
| **Calibration** | prices ≠ outcome frequencies in specific strata | needs power, not cleverness; v2 saw the effect (July longshots) but at n = 95 | S1, S2 |
| **Skill persistence** | some wallets are informed; their prints predict outcomes | uses *who* traded, an axis v2 never touched; on-chain profit is highly concentrated | S3 |
| **Structural** | dependent outcomes must satisfy identities (Σ YES = 1, nested deadlines, ladders) | violations are arithmetic, not forecasts; documented to persist for hours in thin multi-outcome events | S4 |
| **Liquidity provision** | makers pay no fee and receive rebates funded by takers | the only edge that exists by construction; needs recorded L2 | S5 |
| *(optional)* Cross-venue | Kalshi and Polymarket price the same event | lead–lag between venues with different participants | S6 |

### 1.3 What "best possible result" means

Be precise about this, because it shapes the build.

The programme cannot manufacture an edge. What it can do is (a) detect one if it exists at a
magnitude worth having, (b) state the magnitude and capacity, and (c) prove, with the same
rigour, where none exists. The realistic best outcome is a **map**:

- Liquid binaries will very likely prove well calibrated net of costs — that is what M7's null
  already suggests, and the census will say so with a magnitude attached.
- If calibration edges exist they will be in thin, fee-free or multi-outcome strata, worth a
  few cents per share, with monthly capacity in the hundreds of dollars at $1,000 — real, small,
  and lumpy.
- Wallet persistence, if present, will decay with follow lag; the study reports the decay curve
  so the infrastructure requirement is known before any capital is considered.
- Structural violations exist at low frequency; they need a live scanner to capture. The
  programme delivers the scanner as a paper tool and measures what it would have caught.
- Liquidity provision is the most likely positive expectation, and the smallest per unit of
  capital and attention.

A **confirmed edge** in this programme means: net expected return per trade > 0 with a
cluster-robust 90% CI above zero, on two disjoint confirmation sets (§7.5), sign stable across
months, capacity ≥ $50/month at $1,000, and a paper-trading month under the live scanner that
matches the backtest's expectation. That is the bar for the word. Anything less is reported with
its actual label.

If, after the whole programme, nothing clears that bar, the conclusion is that a small
rule-following account has no capturable edge on Polymarket beyond making — and that is worth
more than any curve fitted to the past.

---

## 2. Why the v2 search was narrow

Recorded so the same limits are not rebuilt.

1. **One hypothesis class.** All 44 configurations were directional rules on the 1-minute
   mid-like series. That is the most automated corner of the platform.
2. **Liquidity pre-filter.** `min_volume_asof ≥ $10k` was necessary for survivorship safety on
   a final-volume-filtered sample, but it selected exactly the markets where bots compete.
3. **Sample size.** 400 markets a month from a universe of roughly 400,000. A to-resolution
   cell at price 0.15 has a per-trade return SD of about 2.4; 95 trades give a 90% CI of ±40%.
   The July longshot finding died of variance, not of being wrong.
4. **Two engine distortions.** Fills had no limit-price semantics (16 of 146 survivor fills
   landed above 0.25), and the liquidity cap counted prints on both sides.
5. **Independent-trade bootstrap.** February's 30 crypto longshots were one regime event, not
   30 draws. Trade-level resampling understated every CI.
6. **One period.** Seven months of 2026 is one regime; fees changed inside it.

---

## 3. Engine changes (M8) — land before any new measurement

### 3.1 E1 — Marketable-limit orders

DSL `order` block, on entries and exits:

```jsonc
"order": {
  "type": "LIMIT",            // MARKET | LIMIT ; default LIMIT for all research
  "limit_offset_ticks": 1,    // buy limit = signal price + k ticks; sell limit = signal price − k
  "ttl_bars": 5               // bars the order stays live, then UNEXECUTABLE_LIMIT_EXPIRED
}
```

Semantics under `TRADE_PRINT` (taker-only, as in v2): in each bar within the TTL, the eligible
prints are the same-side prints (E2) at price ≤ limit (buys) or ≥ limit (sells). If any exist,
fill up to the participation cap of their size at their VWAP, quantised against the trader,
never worse than the limit. If none exist, the order waits; when the TTL lapses the trade row
is `UNEXECUTABLE_LIMIT_EXPIRED` with the best price seen in the window recorded. `MARKET`
keeps v2 behaviour and is flagged `MARKET ORDER — NO PRICE PROTECTION` in results.

The v2 `max_fill_vs_signal` idea is subsumed by `limit_offset_ticks`.

### 3.2 E2 — Same-side liquidity

`execution.liquidity_side: SAME_SIDE | BOTH`, default `SAME_SIDE`. A taker buy consumes ask
liquidity; evidence that ask liquidity existed at a price is other takers buying at that price.
Reference VWAP, participation cap and worst-print are computed on taker-BUY prints for buys and
taker-SELL prints for sells. `BOTH` remains available, labelled optimistic.

### 3.3 E3 — Basket trades and `EventView`

Structural strategies buy several tokens at once. Add:

- `bt_trades.basket_id` (nullable) and `basket_role` (`LEG` | `SUMMARY`). Legs are ordinary
  trade rows; the summary row carries Σ cost, Σ fees, Σ payout, net.
- **All-or-nothing execution within one bar.** If any leg is unexecutable under E1/E2 the whole
  basket is `UNEXECUTABLE_BASKET_LEG` and nothing is bought. Partial baskets are not a thing:
  half an arbitrage is a directional bet.
- Exposure counts Σ leg notionals; `max_positions_per_event` counts a basket as one.
- Settlement of a basket is Σ leg settlements; the summary row's `exit_reason` is
  `MARKET_RESOLVED` when all legs have settled.
- `EventView`, look-ahead-safe like `MarketView`: `now`, `event_id`, the sibling markets **open
  at `now`** with each token's price, tick, minimum size and same-side prints in the current bar,
  `catch_all_present` (§4.2), `n_markets_open`. Built from the same `MergedTimeline`. The
  clairvoyant test (§17.4 of v2) gains cases for sibling sets and catch-all status read from the
  future.

### 3.4 E4 — Event-driven exits

`exit.any[]` gains `{"type": "WALLET_SELL", "wallet_ref": "signal"}`: exit when the wallet
whose print triggered the entry prints a taker SELL on the same token at or after the entry
bar. Needed by S3 variant B. The exit signal is the print's bar; execution follows E1.

### 3.5 E5 — Statistics upgrades in `syntha_engine.honesty`

- **Cluster bootstrap.** Resample clusters, not trades: (a) by `event_id`, (b) by UTC calendar
  day. 10,000 resamples each. Report both and use the wider as the headline CI. Trades of the
  same event, or the same day, are one draw.
- **Primary metric for hold-to-resolution studies:** net return per dollar risked, mean over
  trades, `Σ net_pnl / Σ notional` as the pooled estimate. ROI stays as a secondary,
  path-dependent figure.
- **MDE.** For any test reported as negative, the minimum detectable effect at 80% power,
  one-sided 5%, using the cluster-adjusted SE: `MDE = 2.48 × SE`.
- **Capacity.** For any positive candidate: capturable notional per month = Σ over signals of
  `min(requested, participation × same-side print size in the fill window)`, reported at $1,000
  and $10,000 starting capital, with the fraction of signals that would be size-constrained.
- **Fee counterfactual on every study:** results under `HISTORICAL` fees (what happened) and
  `CURRENT` fees (what it would cost today). Decisions use `CURRENT`. An edge that existed only
  while a category was fee-free is not an edge.

### 3.6 E6 — The register

`tools/research/register.py` appends one row per registered configuration, cell, wallet rule or
scan variant to `docs/research/REGISTER.md`:

```
| id | study | family | definition_hash | registered_at | data_seen_at_registration | status | result |
```

`pooled.py`'s best-of-N null takes N from the register, per family. The sweep tools refuse to
run a definition that has no register row.

### 3.7 E7 — Recorder watchlist v2 (starts at M8 because it only pays later)

Replace "top 10 by 24h volume" with a designed watchlist table:

```sql
CREATE TABLE recorder_watchlist (
  clob_token_id text NOT NULL, condition_id text NOT NULL, event_id text,
  stratum text NOT NULL,        -- e.g. 'politics/multi/thin', 'sports/binary/liquid'
  reason text NOT NULL,         -- 'stratum_fill' | 'structural_candidate' | 'manual'
  added_at timestamptz NOT NULL, removed_at timestamptz,
  PRIMARY KEY (clob_token_id, added_at)
);
```

Target 40–60 markets at all times: 4 categories × {binary, multi-outcome} × {thin, liquid},
refilled hourly as markets resolve, plus every event that S4's scanner flags. Store
`last_trade_price.fee_rate_bps` (the only per-fill fee record) on `orderbook_deltas` rows of
type `trade`. Coverage report: token-days recorded, gaps > 5 min, bytes/day. Measured volume in
v2 was ~2.3 GB/day uncompressed for 10 tokens; budget 12 GB/day compressed at 60 tokens and set
Timescale compression after 1 day.

**Acceptance for M8:** golden fixtures for E1 (limit fills, expiry, never worse than limit),
E2 (same-side cap), E3 (all-or-nothing basket, exposure count), E4; cluster bootstrap reproduces
the trade-level CI on a fixture with one trade per cluster and widens it on a fixture with
correlated clusters; MDE and capacity fields present in every `metrics.json`; register enforced;
recorder watchlist populated and coverage report running. `ENGINE_VERSION` 0.7.0. Re-running
BT-20260914-00041 under E1/E2 is documented in `DATA_NOTES.md` as the reference for how much
the two fixes change a known result.

---

## 4. Data workstreams

### 4.1 D1 — Calibration census (the power fix)

**What it is.** A sparse ingest mode that fetches, per market, only what calibration needs:
prices at fixed offsets, resolution, and the prints around each offset that decide whether a
fill was possible. No 1-minute bars. About 3.1 requests per market instead of ~15, and ~12 rows
per market instead of tens of thousands.

**Sampling frame.** For each calendar month `M` from 2025-07 to 2026-10: all Gamma markets with
`endDate` in `M` (listing per month, `closed=true`, keyset paging, WAF-cursor routing from M7).
The sampler sees `conditionId`, `feeType`, `startDate`/`acceptingOrdersTimestamp`, `endDate`,
`negRisk`, event membership — **never volume, liquidity or outcome**. Strata:

| Dimension | Values | Note |
| --- | --- | --- |
| category | from `feeType`, fallback tags; geopolitics = fee-free | as in `DATA_NOTES.md` |
| duration class | `ultra_short` (≤ 60 min), `intraday` (≤ 24 h), `multi_day` (≤ 30 d), `long` (> 30 d) | scheduled end − open; known at listing |
| structure | `binary`, `sibling` (member of an event with ≥ 3 markets) | |

Allocation: up to 2,000 markets per (category, duration class) per month, uniform by seed,
`ultra_short` capped at 500 and excluded from primary analyses (5/15-minute crypto is a latency
game with a different fee curve; it is measured, not pooled). Expected ≈ 15,000–25,000 markets
a month. The manifest records seed, frame size and allocation.

**Fetch per market.**

| Data | Request | Notes |
| --- | --- | --- |
| metadata | Gamma `/markets?id=…` batched 50 | both `closed` states, as in M1 |
| prices | CLOB `prices-history` per token, `fidelity=60`, window `[end − 15 d, end]` | one call per token; `fidelity=1` only for the `[end − 6 h, end]` sub-window on `intraday`/`ultra_short` |
| prints | Data `/v2/trades?condition=`, newest first, page until earliest print < `end − 8 d` or 10 pages | `prints_truncated=true` if the cap is hit; tiers for uncovered offsets are null, never guessed |
| resolution | `/v2/resolutions` batched 20 | both row shapes, Gamma/chain agreement rule from M1 |

Rate: the census runs at `--rps 20` (documented limits: CLOB 100/s, Data v2 trades 30/s, Gamma
30/s; the trades endpoint binds). 20,000 markets × 3.1 ≈ 62,000 requests ≈ 55 minutes per
month of data. Ten months ≈ 10 hours. Resumable checkpoints as in M1.

**Offsets.** Relative to scheduled end: `−7d, −3d, −24h, −6h, −1h, −15m`. Relative to open:
`+1h, +24h`. Each offset is skipped when it precedes the market's open or follows its close.

**Observation row.** For each (market, token, offset):

```sql
CREATE TABLE census_observations (
  census_id text NOT NULL, condition_id text NOT NULL, clob_token_id text NOT NULL,
  event_id text, offset_label text NOT NULL, ts timestamptz NOT NULL,
  mid numeric(8,6),                              -- CLOB sample at or before ts
  staleness_minutes int,                         -- minutes since last print before ts
  fill_10m numeric(8,6),                         -- VWAP of taker-BUY prints in [ts, ts+10m] at ≤ mid + 1 tick; null if none
  fill_30m numeric(8,6),                         -- same, 30-minute window
  fill_size_10m numeric(18,6),                   -- same-side printed shares in the window at ≤ limit
  liq_24h numeric(18,2),                         -- same-side print notional in [ts−24h, ts]; null if prints truncated
  fee_entry numeric(18,6),                       -- taker fee on a 1-share fill at fill_10m, HISTORICAL schedule
  fee_entry_current numeric(18,6),               -- same under CURRENT schedule
  payout numeric(8,6),                           -- 1, 0, or 0.5 (INVALID); from SettlementTable only
  PRIMARY KEY (census_id, clob_token_id, offset_label)
);
CREATE TABLE census_markets (
  census_id text NOT NULL, condition_id text NOT NULL,
  category text, duration_class text, structure text, deadline_type text,   -- §4.4
  n_outcomes int, prints_truncated boolean, resolution text,
  PRIMARY KEY (census_id, condition_id)
);
```

`payout` is engine-restricted in the same sense as v2 §7: the study code that builds
observations receives it from the settlement path, and the feature side never does.

**Executable return per observation:** `r = (payout − fill_10m − fee_entry) / fill_10m`
(no exit fee at settlement, per `DATA_NOTES.md`). Rows with `fill_10m` null contribute to
mid calibration only, and the study reports the fill rate per cell — an edge that exists only
where nothing fills is not capturable.

### 4.2 D2 — Event-complete ingest

The M1/M6 samplers draw markets independently, so siblings are mostly absent and no set
identity can be checked. Add `ingest-events`: sample **events** with ≥ 3 markets (uniform by
seed within category, ~200 per month), fetch **every** sibling market's bars (1-minute for the
last 15 days, hourly before) and prints. Store:

```sql
ALTER TABLE events ADD COLUMN n_markets int, ADD COLUMN neg_risk boolean;
CREATE TABLE event_membership (
  event_id text NOT NULL, condition_id text NOT NULL,
  listed_at timestamptz NOT NULL,       -- when the market became part of the event (Gamma createdAt / acceptingOrdersTimestamp)
  is_catch_all boolean NOT NULL,        -- 'Other' / 'None of the above' / 'No winner' by text rule, reviewed
  PRIMARY KEY (event_id, condition_id)
);
```

`listed_at` makes the sibling set point-in-time: a candidate added on day 20 is not in the
set on day 5. `catch_all_present_at_t` is true only if a catch-all market was listed at `t`.
A set without a catch-all is **not assumed exhaustive**; Σ YES < 1 there is not an arbitrage,
because the true outcome may be one nobody listed. The scanner records both cases and reports
them separately.

### 4.3 D3 — Wallet histories

`trade_prints.proxy_wallet` already exists on every row. Phase A of S3 uses it as is. Phase B
needs full per-wallet histories for the wallets that matter:

- Identify, per study month `M`, the wallets that rank in the top 1,000 by in-sample realized
  P&L as of the start of `M` (point-in-time; §5.3).
- Fetch each one's full trade history from the Data API's per-user endpoints. **Verify at build
  time:** whether `/v2/trades` accepts a `user` filter; else `/activity?user=&type=TRADE`;
  else `/positions?user=` plus `/trades?user=` (v1, 10,000-offset cap). Record which was used.
- Store in `wallet_prints` with the same shape and dedup key as `trade_prints`, plus
  `fetched_for_month`. Never merge into `trade_prints`: sampled-market prints and
  wallet-complete prints answer different questions.

Wallet addresses are public on-chain data and pseudonymous. They stay inside the app and the
research artefacts; nothing in this programme publishes a wallet leaderboard.

### 4.4 D4 — Question parsing: deadlines, ladders, links

`tools/research/parse_questions.py`, run over every market the census or event ingest touches:

- `deadline_type`: `BY` ("by", "before", "prior to" + date), `ON` ("on", "at" + date, or a
  fixture/game), `NONE`. Stored on `census_markets` and `markets`.
- `subject_key`: the question with the date phrase and the numeric threshold removed,
  normalised. Two markets share a subject only if their `subject_key` strings are identical
  after normalisation — no fuzzy matching in v3; recall is traded for zero false links.
- `market_links` (point-in-time, `valid_from` = the later market's `listed_at`):

```sql
CREATE TABLE market_links (
  link_type text NOT NULL,      -- NESTED_DEADLINE | LADDER | NEGRISK_SET
  a_condition_id text NOT NULL, b_condition_id text NOT NULL,
  relation text NOT NULL,       -- 'P(a) <= P(b)' with the reason: 'earlier deadline', 'higher strike'
  valid_from timestamptz NOT NULL,
  source text NOT NULL,         -- 'parsed' | 'manual'
  PRIMARY KEY (link_type, a_condition_id, b_condition_id)
);
```

`NESTED_DEADLINE`: same subject, dates `D_a < D_b` → `P_yes(a) ≤ P_yes(b)`.
`LADDER`: same subject and date, thresholds `X_a > X_b` ("above $X") → `P_yes(a) ≤ P_yes(b)`.
A parsed link is reviewed by rendering both questions side by side in the app; unreviewed
links are used in scans but flagged.

### 4.5 D5 — Kalshi (optional, S6 only)

Public market-data endpoints (`/trade-api/v2/markets`, `/markets/{ticker}/candlesticks`,
`/markets/{ticker}/trades`). Verify endpoints and terms of use at build time; store only what the
terms permit. `venue_links(polymarket_condition_id, kalshi_ticker, source, reviewed)` for the
top 50 shared events, manual. `kalshi_bars` at the finest permitted grain.

### 4.6 Data risks

- **1-minute retention.** Data API v2 keeps 1-minute data ≥ 7 days only; CLOB v1 still serves
  it historically. The census is designed not to depend on 1-minute bars except for the last
  6 hours of short markets; the event ingest does depend on them for the last 15 days. Backfill
  as early as possible and never delete `raw/`.
- **Fee era.** 2025 months are fee-free; every 2025 result is reported under `CURRENT` fees for
  decision-making (§3.5).
- **Storage.** Census tables are small (≈ 200k rows per month). Event ingest adds ≈ 200 events ×
  8 markets × 2 tokens × 21,600 bars ≈ 70M bars per month; acceptable, compress after load.

---

## 5. Studies

Each study is a directory `research/studies/<id>/` containing `registration.yaml` (§7.6),
the discovery and confirmation commands, and the generated report. Studies are research
artefacts; their positive findings become DSL strategies and ordinary runs.

### 5.1 S1 — Executable calibration surface

**Hypothesis.** In identifiable strata, tokens pay out more (or less) than their executable
price, net of fees, by enough to matter.

**Estimand.** For each cell of (category × price bucket × offset), with secondary
stratifications by liquidity tier, `deadline_type`, structure and duration class:
`E[r]`, the mean executable return (§4.1), cluster-bootstrapped by `event_id` and by day;
plus mid calibration `E[payout − mid]`, fill rate, and capacity.

Price buckets on the *fill* price: `[0.01,0.03) [0.03,0.10) [0.10,0.20) [0.20,0.35)
[0.35,0.50) [0.50,0.65) [0.65,0.80) [0.80,0.90) [0.90,0.97) [0.97,0.99]`.

**Power.** With `SD(r) ≈ √((1−p)/p)` for a token at price `p` and a design effect of ~1.4 for
clustering, the MDE at 80% power is:

| price | SD(r) | n = 300 | n = 1,000 | n = 3,000 |
| --- | --- | --- | --- | --- |
| 0.05 | 4.4 | 87% | 48% | 28% |
| 0.15 | 2.4 | 48% (7¢) | 26% (4¢) | 15% (2.3¢) |
| 0.50 | 1.0 | 20% (10¢) | 11% (5.5¢) | 6% (3¢) |
| 0.90 | 0.33 | 6.7% (6¢) | 3.7% (3.3¢) | 2.1% (1.9¢) |

The census delivers ≈ 40,000 token-observations per offset per month; populous cells reach
several thousand observations over six discovery months, which is what a 2–4¢ edge needs.
Cells below 300 observations are reported as underpowered with their MDE and never selected.

**Pre-registered selection rule (discovery, set D, §7.5).** A cell is a candidate if:
`E[r]` under `CURRENT` fees > 0 with the wider cluster 90% CI above zero; n ≥ 500; fill rate
≥ 30%; sign positive in ≥ 4 of 6 months of 2025 H2 **and** ≥ 3 of 4 of 2026 Feb–May; and the
cell survives Benjamini–Hochberg at 10% FDR across all primary cells tested (the register holds
the count). At most 8 candidates, by `E[r] × capacity`.

**Confirmation.** Each candidate becomes a DSL strategy (`universe` = the cell definition,
`entry` = price in bucket at the offset window, hold to settlement, LIMIT +1 tick, TTL 5,
2% of capital, `max_positions_per_event 1`) and runs through the engine on C1 and then C2,
Realistic and Conservative, with the permutation test. Verdicts per §7.5.

**Output.** `research/studies/s1/calibration.parquet`, heatmaps per offset, the candidate
list, and the negative-space table: every stratum with its MDE.

### 5.2 S2 — Deadline decay (a stratification of S1 plus one dynamic rule)

**Hypothesis.** `BY`-type markets that have not resolved are sticky: the YES price fails to
decay as the deadline approaches, so NO is underpriced in the last days.

**Test 1 (observational, inside S1):** `E[r]` for NO tokens on `BY` markets vs `ON` markets,
same price bucket and offset. **Test 2 (dynamic):** a registered DSL rule — `deadline_type = BY`,
NO price in `[0.50, 0.90]` at `−72h`, YES price change over the prior 7 days ≥ −10% (i.e. it has
not decayed) — run through the engine on D, then C1/C2. Both are register rows.

### 5.3 S3 — Wallet skill persistence

**Hypothesis.** Wallets with a positive point-in-time realized record continue to be
informative; following their taker buys, with a realistic lag, is profitable net of costs.

**Point-in-time record of wallet `w` at time `t`:** over `w`'s prints on markets whose
`resolved_at < t`: `n_resolved_markets`, net realized P&L (payout or sell proceeds − cost, fees
under HISTORICAL), hit rate, `concentration` = share of P&L from the single best market,
category split. Computed from `trade_prints` (Phase A, records are partial and lower-bound) or
`wallet_prints` (Phase B, complete). Never from anything resolved at or after `t`.

**Exclusions (classified point-in-time, stored on the record):**
`HEDGED` — has bought both tokens of the same market within 24 h;
`HIGH_FREQ` — median > 200 prints/day over the trailing 30 days;
`SCALPER_98` — > 80% of buys at ≥ 0.95 (kept as its own stratum, not followed);
`ONE_HIT` — `concentration` > 0.5 with fewer than 20 resolved markets.

**Signal.** A taker BUY print of notional ≥ `min_notional` (registered values: 100, 500) by a
wallet in the top decile of eligible wallets (≥ 20 resolved markets) as of `t`. The follower
enters at `t + lag` with LIMIT +2 ticks, TTL 5 bars, 2% of capital; variants:
A — hold to settlement; B — exit on the wallet's taker SELL (E4). Registered lags: 1, 5, 15,
60 minutes. The result is reported as a **decay curve** of `E[r]` against lag: it tells whether
a human, a bot, or nobody could capture it.

**Nulls.** (i) Permutation: same timestamps, a random other wallet's BUY print in the same
category (does *who* matter?). (ii) Size-matched: random prints of the same notional tier
(does *size* explain it — large orders move the price and the follower buys the move?).

**Selection and confirmation.** Phase A on 2026-02…05 (D) is hypothesis-generating only; a
positive Phase A triggers Phase B fetches. Phase B: records from complete histories, discovery
on D, confirmation on C1 and C2 with the §7.5 verdicts. `min_notional` and lag are chosen on D
and frozen.

### 5.4 S4 — Structural violations: measurement, then a paper scanner

**Hypothesis.** Set identities are violated often enough, long enough and in executable size to
be worth a live scanner at $1,000.

**Scan A — neg-risk sets.** On event-complete data, per minute, with the point-in-time
sibling set: `S_yes = Σ mid_yes`, `S_no = Σ mid_no`. Violation when `S_yes < 1 − c` or
`S_no < (N − 1) − c`, where `c` = Σ leg fees (CURRENT) + one tick per leg. An **episode** is a
maximal run of violating minutes; record depth, duration, `catch_all_present`, and, per leg,
whether same-side prints at ≤ limit existed inside the episode (executability), and the
capturable size = min over legs of participation-capped print size.

**Scan B — nested deadlines and ladders.** On `market_links`, per minute where both markets
are open: violation when `P_yes(a) > P_yes(b) + c`. Position: buy NO(a) and YES(b); payoff ≥ 1
per unit in every state, cost < 1 when violated. Same episode accounting.

**Engine check.** Every executable episode is replayed as a basket (E3) under Realistic and
Conservative; settlement proves the identity held (and counts the cases where it did not:
INVALID, or a set that was not exhaustive).

**Registered decision rule.** The scanner is worth building as a paper tool if, on D: ≥ 5
independent events per month with an executable episode; median episode duration ≥ 2 minutes
(a human-speed bound; also report ≥ 30 s); net capturable profit at $1,000 ≥ $30/month with a
cluster 90% CI above $10. Confirmation on C1/C2 measures whether frequency and duration are
stable, and the recorder (E7) measures the same episodes on live books for the events it watched.

**Non-goal.** No claim about liquid sports binaries: published order-book studies find
single-market anomalies there last seconds. The scan covers them only to document that.

### 5.5 S5 — Maker simulation (only after ≥ 30 days of E7 recording)

**Hypothesis.** Passive quoting inside the spread earns the maker rebate and spread capture in
excess of adverse selection, in some strata.

**Simulator** (`BOOK` model, scoped to making; v2 §11.4 remains gated for taking):
quotes at `mid ± k ticks` refreshed each minute from recorded L2; a quote is filled only when a
recorded print trades **through** it (price strictly better than the quote from the taker's
side, size ≥ quote size) — conservative on queue position; rebate from the market's
`feeSchedule.rebateRate` × the taker fee on the fill (`last_trade_price.fee_rate_bps` when
present); adverse selection = mark 5 and 30 minutes after the fill; inventory capped per token
and per event; inventory flattened at resolution. Liquidity-rewards programme payments are
**not** modelled (say so on every result).

**Registered decision rule.** Net expectancy per quote-day > 0 with a cluster (market-day) 90%
CI above zero over ≥ 30 market-days per stratum; stable across two non-overlapping recording
fortnights.

### 5.6 S6 — Kalshi lead–lag (optional)

**Hypothesis.** On shared events, one venue's price predicts the other's next move.

**Test.** Aligned 5-minute bars; regress Polymarket's forward return on Kalshi's lagged return
and Polymarket's own; and a divergence rule: when `|P_poly − P_kalshi| > 3¢`, which converges.
A registered DSL rule executes the divergence signal under Realistic. Verdicts per §7.5. Lowest
priority; skip if the terms of use do not permit storage.

---

## 6. Stage 4 protocol (pre-registered)

This section is copied verbatim into `docs/RESEARCH_PROTOCOL.md` under "Stage 4" before M9's
discovery command runs.

### 6.1 Data sets and disclosure

| Set | Months | Prior exposure | Role |
| --- | --- | --- | --- |
| **D** (discovery) | 2025-07 … 2025-12 | none | discovery |
| **D** (discovery) | 2026-02 … 2026-05 | fresh samples of months whose 400-market samples were used in M7 | discovery, disclosed |
| **C1** (confirmation, disclosed) | 2026-06 … 2026-08 | fresh samples of months used in M6/M7 holdouts | first confirmation |
| **C2** (confirmation, untouched) | 2026-01, 2026-09, 2026-10 | none; 2026-10 fetched once its markets resolve (mid-November) | final confirmation |

Fresh samples of previously used months are new markets but the same regime; that is why C2
exists and why C2 alone carries the word "confirmed".

### 6.2 Verdicts

- **Confirmed:** clears D by the study's selection rule; on C1 and on C2 separately, `E[r]`
  under CURRENT fees > 0 with the wider cluster 90% CI above zero and the sign positive in ≥
  2/3 of each set's months; Conservative preset also positive pooled over C1 ∪ C2; capacity ≥
  $50/month at $1,000; then one paper-trading month (§10) within the backtest's 90% interval.
- **Promising / unconfirmed:** clears D and C1 but not C2, or C2 pooled positive with a CI
  spanning zero. Added to the app as an example with the record and the word "unconfirmed".
- **Rejected:** otherwise. Reported with MDE.

### 6.3 Multiple comparisons

The register counts every primary cell, rule, wallet variant and scan variant. Cell-wise
selection in S1/S2 uses BH at 10% FDR. Every study's report states N from the register, the
expected best-of-N by chance from the stored permutation variants, and how often chance beats
the actual best. No configuration is changed after registration; extensions are new register
rows with their own accounting.

### 6.4 What this cannot show

Sixteen months across two years is still one platform in one era. A confirmed edge is a
reason to paper trade, not to risk money; a confirmed edge with capacity of $50/month is a
finding about the market, not an income.

---

## 7. Cross-cutting requirements

### 7.1 Point-in-time, extended

| Object | As-of rule |
| --- | --- |
| liquidity tier | same-side print notional in the 24 h before the offset; null if truncated |
| wallet record | markets resolved strictly before `t`; classification flags from trailing windows ending at `t` |
| sibling set | markets with `listed_at ≤ t`; catch-all status at `t` |
| links | `valid_from ≤ t` |
| fee schedule | the schedule at the fill timestamp (HISTORICAL) or today's (CURRENT), never mixed |

### 7.2 Honesty layer on studies

Everything the engine shows on a run, a study report shows on a finding: disclaimer first,
verdict second, then numbers; CI, permutation percentile, MDE, capacity, fee mode, fill rate,
data-quality block, register N.

### 7.3 MDE on negatives

Every "no effect" line in a report carries `MDE@80%` and n. The programme's final report has
a negative-space table: for each stratum, the largest edge that could have existed undetected.

### 7.4 Capacity on positives

Every positive carries capacity at $1,000 and $10,000, the share of signals that would be
size-constrained, and the monthly count of independent events.

### 7.5 Verdict vocabulary

Only `confirmed`, `promising / unconfirmed`, `rejected`, `underpowered`. No other adjectives
in reports.

### 7.6 Registration template

```yaml
study: s1_calibration
registered_at: 2026-09-15T09:00:00Z
registered_by: <name>
data_seen_before_registration:
  - march..july-2026-liquid-400 (M6/M7), february/august-2026-liquid-400 (stage 3)
discovery_set: [2025-07..2025-12, 2026-02..2026-05 fresh]
confirmation_sets: {C1: [2026-06..2026-08 fresh], C2: [2026-01, 2026-09, 2026-10]}
estimand: mean executable return per token-observation, CURRENT fees
primary_cells: category x price_bucket x offset   # count written by register.py
selection_rule: |
  CI90_cluster_lower > 0; n >= 500; fill_rate >= 0.30;
  sign positive in >= 4/6 of 2025H2 months and >= 3/4 of 2026 Feb-May;
  BH FDR 10% across primary cells; at most 8 candidates by E[r] x capacity
confirmation_rule: PRD v3 §6.2
frozen_parameters: {limit_offset_ticks: 1, ttl_bars: 5, sizing: 2% of capital, participation: 0.10}
what_would_kill_it: |
  fill rate < 30% in the candidate cells; sign flips between 2025H2 and 2026;
  effect present under HISTORICAL but not CURRENT fees
```

---

## 8. App surface (minimal)

- `/research` — studies with status, register N, last run, links to reports.
- `/research/calibration` — heatmap explorer: cell → n, `E[r]` with CI, mid calibration, fill
  rate, MDE, capacity; toggles for fee mode, offset, liquidity tier, `deadline_type`; every
  cell links to its observations. Colour encodes CI-lower, not the point estimate, so noise
  does not look like signal.
- `/research/wallets` — point-in-time record browser for a chosen `as_of` date; decay curves
  from S3. In-app only.
- `/research/structural` — episode browser with replay (both/all legs on one chart), the basket
  replay, and the live-scanner view fed by the recorder for watched events (§10).
- `/data/recorder` — watchlist and coverage.
- Reports: the M6 generator extended to studies; every report opens with the verdict block.

Design rules from v2 §19 apply. One addition: **no cell, wallet or episode is ever coloured
green on a point estimate alone**.

---

## 9. Milestones

Dependencies: M8 → (M9 ∥ M10) → M11 → M12 → M13. E7 (recorder v2) is part of M8 because
S5 needs weeks of recording that cannot be backfilled.

### M8 — Engine and instruments (acceptance in §3)
E1–E7. Reference re-run of BT-20260914-00041 documented.

### M9 — Census and S1/S2
**Acceptance:** census ingest for one 2025 month completes resumably at 20 rps with a
manifest; `census_observations` rebuilds bit-identically; the S1 registration is committed
before the discovery command; discovery report produced for D with heatmaps, candidate list (or
"none"), negative-space table and register N; candidates run as DSL strategies on C1, then C2,
with verdicts. S2 tests registered and reported in the same run.

### M10 — S3 wallet persistence
**Acceptance:** point-in-time records with classification flags and a causality test (a
record at `t` cannot change when prints after `t` are removed); Phase A decay curves on D;
Phase B endpoint verified and histories fetched for the top 1,000 wallets per month if Phase A
is positive; registration, discovery, C1/C2 verdicts.

### M11 — Event ingest, question parsing, S4 scans
**Acceptance:** `event_membership` and `market_links` populated, links reviewed in-app;
Scan A and Scan B episode tables with executability; basket replays match hand-computed
payoffs on golden fixtures (exhaustive set, non-exhaustive set, INVALID leg); decision-rule
report on D and C1/C2; watchlist automatically includes flagged events.

### M12 — S5 maker simulation
**Acceptance:** ≥ 30 days of E7 data; simulator golden fixtures (fill only on trade-through,
rebate arithmetic, adverse-selection marks, inventory cap); per-stratum results with the
registered rule; a second recording fortnight for stability.

### M13 — S6 Kalshi (optional) and programme report
**Acceptance:** terms verified; `venue_links` reviewed; lead–lag results with verdict; the
programme report (§11).

---

## 10. From finding to paper trading (and no further)

For any `promising` or `confirmed` finding, the paper path is:

1. **Live scanner.** `services/scanner/` subscribes to the CLOB websocket for the finding's
   universe (or, for S4, for the events in the watchlist), evaluates the finding's rule on live
   data every minute, and writes *intended* orders with the price it would have accepted.
2. **Paper fills** are decided by the recorder's next minute of prints under the same E1/E2
   rules the backtest used. No order is ever placed.
3. **One month** of paper results is compared with the backtest's 90% interval. Inside: the
   finding keeps its label. Outside: it drops to `unconfirmed` and the discrepancy is written
   up (latency, fills, regime).

Nothing in v3 connects a wallet, signs a transaction, or touches Polymarket's trading
endpoints. This is the last step of the programme by design.

---

## 11. Programme report — definition of done

The programme is complete when `research/PROGRAMME_REPORT.md` exists and contains, in order:

1. The verdict on each study in the vocabulary of §7.5, with register N.
2. The map: for every stratum, either a confirmed/promising edge with magnitude, CI, capacity
   and fill rate, or the MDE that bounds what could have been missed.
3. The decay curve for wallet following and the executability distribution for structural
   episodes, whether or not either was positive.
4. What the recorder captured live versus what the backtests expected, for every paper month.
5. The list of everything tried, from the register.
6. The plain-language answer to §1.1, including "nothing clears the bar" if that is the answer.

---

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| 1-minute history stops being retrievable | census depends on hourly grain except last 6 h of short markets; backfill first, `raw/` is permanent |
| Rate limits or WAF at 20 rps | per-endpoint token buckets, `Retry-After`, cursor routing from M7, resumable checkpoints |
| Non-exhaustive neg-risk sets | catch-all rule (§4.2); non-exhaustive episodes reported separately, never as arbitrage |
| Wallet records are partial (Phase A) | Phase A is hypothesis-generating only; confirmation needs Phase B |
| Copied wallets are hedged or insiders | classification flags; the study measures return net of the follower's own costs and never places orders |
| The programme itself is a multiple-comparison machine | the register, FDR on cells, C2 untouched months, MDE on every negative |
| Kalshi terms of use | verify before storing; S6 is optional |
| A positive result gets used before paper trading | §10 is the only path, and it ends at paper |

---

## Appendix A — Kickoff prompt for Claude Code

> Read `docs/PRD.md` (v2) and `docs/PRD_v3.md` in full. Implement M8 only, then stop and
> summarise. Constraints: the engine stays I/O-free; `LIMIT` is the default order type for
> research; the liquidity cap counts same-side prints; a basket is all-or-nothing; the
> honesty layer reports cluster CIs, MDE and capacity; no research command runs without a
> register row. Re-run BT-20260914-00041 under the new engine and record the difference in
> `docs/DATA_NOTES.md` before starting M9. Verify every endpoint used by the census against the
> live docs first.

## Appendix B — Endpoints to verify at build time

- Gamma listing and batched `/markets?id=` (M1 behaviour), `closed` flag, keyset paging.
- CLOB `prices-history` at `fidelity=60` for 2025 markets; 15-day window cap.
- Data `/v2/trades?condition=` paging depth; whether a `user` filter exists; `/activity`,
  `/positions` per-user shapes.
- `/v2/resolutions` both row shapes.
- Websocket `market` channel events, including `fee_rate_bps` on `last_trade_price`.
- Kalshi public market-data endpoints and terms (S6 only).

## Appendix C — Formulas

- Executable return: `r = (payout − fill − fee_entry) / fill`, fill = same-side VWAP at ≤
  mid + k ticks in the fill window, quantised against the trader.
- Return SD at price p (hold to resolution): `SD(r) = √((1 − p) / p)`.
- Cluster bootstrap: resample clusters with replacement, recompute the statistic; 10,000
  resamples; report percentile 5–95.
- MDE (80% power, one-sided 5%): `2.48 × SE_cluster`.
- Capacity per month: `Σ_signals min(requested_notional, participation × same-side print
  notional in the fill window)`, at $1,000 and $10,000.
- Best-of-N null: per register family, draw one pooled null statistic per registered item
  from its stored permutation variants, take the max, repeat 4,000 times.
