# S1b stage 3 — out-of-sample confirmation of R-0793 (crypto, [0.03,0.10), −15m, maker side)

## Registered definitions (verbatim, fixed)

Register rows (`docs/research/REGISTER.md` at tag `pause-2026-09-16`):

```
| R-0769 | s1b_resting_order | s1b_resting_order | fb142c59198cc991 | 2026-09-16T12:26:21Z | Everything S1 and S2 saw (research/studies/s1_calibration/registration.yaml, s2_deadline_decay/registration.yaml).; The full S1 taker calibration surface on D (research/studies/s1_calibration/report, pass 3, 2026-09-16) and the S2 confirmation runs on D, C1 and C2. This registration is informed by D — see motivation — so D can only be its discovery set; nothing of C1 or C2 was examined for maker returns.; The fourteen census months are built; prints and hourly/1-minute bars for them are in the normalized layer (dataset version 8cac86a6). No maker-side statistic has been computed from any of it. | running | D done; C1 + C2 (2026-01) done 2026-09-16: crypto [0.03,0.10) -15m rejected, four cells underpowered, all provisional until 2026-09 joins C2 — research/studies/s1b_resting_order/README.md |
| R-0793 | s1b_resting_order | s1b_resting_order:cells | ada0d4fb552fa0fd | 2026-09-16T12:26:21Z | Everything S1 and S2 saw (research/studies/s1_calibration/registration.yaml, s2_deadline_decay/registration.yaml).; The full S1 taker calibration surface on D (research/studies/s1_calibration/report, pass 3, 2026-09-16) and the S2 confirmation runs on D, C1 and C2. This registration is informed by D — see motivation — so D can only be its discovery set; nothing of C1 or C2 was examined for maker returns.; The fourteen census months are built; prints and hourly/1-minute bars for them are in the normalized layer (dataset version 8cac86a6). No maker-side statistic has been computed from any of it. | paused | D 2025-07..2026-05: n=1024, events=276, loss events=6, r_maker=+0.051, 90% CI +0.037..+0.062, MDE=0.019, AS30=+0.049, capacity=$332/mo; rule passed on D (candidate for C1/C2, not run); confirmation 2026-09-16: C1 pass, C2 fail (loss events 16/5), verdict rejected (provisional); paused 2026-09-16: pending the 2026-09 census month (completes C2) and 2026-10; provisional result stands |
| R-0946 | s1b_resting_order | s1b_resting_order:confirmation | ee194dd6d717e19f | 2026-09-16T17:16:08Z | The D discovery run of S1b (report at commit e56cd48: five cells passing the rule, one without a loss event); no C1/C2 print loaded for this study | paused | C1 (2026-06..08) then C2 (2026-01) run 2026-09-16, realistic + conservative, losing events per set: politics [0.20,0.35) -6h: C1 underpowered, C2 underpowered, loss events 5/4 -> underpowered (provisional); crypto [0.03,0.10) -15m: C1 pass, C2 fail, loss events 16/5 -> rejected (provisional); politics [0.03,0.10) -1h: C1 underpowered, C2 underpowered, loss events 1/0 -> underpowered (provisional); weather [0.03,0.10) -1h: C1 underpowered, C2 underpowered, loss events 4/3 -> underpowered (provisional); politics [0.01,0.03) -1h: C1 underpowered, C2 underpowered, loss events 0/0 -> underpowered (provisional); paused 2026-09-16: pending the 2026-09 census month (completes C2) and 2026-10; provisional result stands |
```

`research/studies/s1b_resting_order/registration.yaml`:

```yaml
unit_of_observation: >
  One taker-BUY print p (price, size, time t_p) on a census token inside the fill window [t_offset, t_offset + 10 min] of a census observation (§4.1 offsets). All taker-BUY prints in the window count, not only those within a tick of mid (S1's fill rule); a print is the evidence that a resting order at that price on the other side was filled.
estimand: >
  Return to a maker whose resting order was the other side of print p and who holds the complement to resolution:
    r_maker = (p − payout_token) / (1 − p)
  (the maker sold the token at p, equivalently bought the complement at 1 − p; the complement pays 1 − payout_token). No taker fee. Rebate 0 in the headline; sensitivity: rebate = 0.25 × the taker fee the taker paid on p under the category's CURRENT schedule, added to the maker's proceeds (HISTORICAL rebates would be 0 on the fee-free 2025 months, docs/DECISIONS.md "M9 prep"). Per cell: equal-weight mean over prints (headline) and size-weighted mean (reported); cluster bootstrap by event_id and by UTC day, the wider 90% interval is the headline; MDE@80% = 2.48 × cluster SE on every cell.
adverse_selection: >
  Mark the position 5 and 30 minutes after the fill: AS_Δ = (p − mark_Δ) / (1 − p), where mark_Δ is the token's 1-minute bar price at t_p + Δ where a 1-minute bar exists for that minute (source `prices_history`, staleness ≤ Δ), otherwise the first print of any side at or after t_p + Δ within 24 hours, otherwise null (counted, never imputed). Reported per cell next to r_maker; a cell whose r_maker is positive but whose AS_30 is strongly negative is a cell where the maker is picked off and rescued by resolution, and is said so.
exclusions:
  - ultra_short markets (question window ≤ 60 min or end − open ≤ 60 min) are measured and reported separately, never pooled into primary cells (the pass-1 S1 artefact)
  - prints whose token has no payout (unresolved or invalid market) are dropped and counted
  - prints at p ≤ 0.01 or p ≥ 0.99 are outside every bucket
  - a month enters D only if its census build passed acceptance
frozen_parameters:
  fill_window_minutes: 10
  prints_used: all taker-BUY prints in the window (side = BUY on the token)
  taker_fee_for_rebate: CURRENT schedule of the category at price p (categories without a schedule pay 0)
  rebate_sensitivity: 0.25
  marks_minutes: [5, 30]
  mark_fallback_horizon_hours: 24
  participation: 0.1
  sizing: 2% of $1,000 per window
  bootstrap_resamples: 10000
  seed: 20260916
confirmation_criteria:
  candidates: 'The five cells in report/summary.json as written by the D run (commit e56cd48), in that order,
    none dropped or added — including politics [0.01,0.03) −1h, which the report flags "no tail observed";
    it is carried so the flag can be tested out of sample.
  
    '
  presets:
    realistic: fill at the taker's print price p (the maker was at the front of the queue) — the deciding
      preset
    conservative: fill one tick worse for the maker, q = p − 0.01 (queue position costs a tick) — the robustness
      arm
    rebate: 0 in both; the 25% sensitivity is reported
  sets:
    C1:
    - 2026-06
    - 2026-07
    - 2026-08
    C2:
    - 2026-01
  order: C1 first, its table written, then C2; no parameter changes between sets
  per_set_rule: "On the realistic preset, per set:\n- decisive: n >= 300 prints and >= 5 losing events (distinct\
    \ events in which the token paid out);\n- pass: decisive, wider cluster 90% CI above zero, r_maker positive\
    \ in a majority of the set's\n  months, capacity >= $50/month at $1,000 with 10% of flow;\n- fail: decisive\
    \ and not pass;\n- underpowered: not decisive (MDE reported).\nLosing-event counts are reported per\
    \ set and per preset.\n"
  verdict: 'confirmed: pass on C1 and on C2, and the conservative preset''s pooled C1 ∪ C2 return > 0;
  
    rejected: fail on C1 or on C2, or conservative pooled C1 ∪ C2 <= 0 while decisive;
  
    promising-unconfirmed: pass on C1 and C2 underpowered;
  
    underpowered: C1 underpowered.
  
    '
  reported_alongside: event-weighted return, 2025 H2 / 2026 split, deadline-type (BY / ON / NONE) split,
    adverse selection at 5 and 30 minutes, size-weighted return
```

Cell R-0793: category crypto, price bucket [0.03,0.10) on the taker's print price, offset −15m (window [end − 15 min, end − 5 min)), maker side (the resting order the taker-BUY print hit). Bar (per_set_rule): n ≥ 300 prints and ≥ 5 losing events, wider cluster 90% CI above zero, positive in a majority of the set's months, capacity ≥ $50/month at $1,000 with 10% of flow. Bootstrap: 10,000 resamples by event_id and by UTC day, wider interval; seed 20260916.


Generated 2026-10-12T22:24:40+00:00 by `tools/research/s1b_stage3.py replay`. Confirmation set: crypto-category markets with scheduled end 2026-09-01 … 2026-10-12 (inclusive), resolved, non-ultra_short, with a −15m observation; months counted by scheduled end: 2026-09, 2026-10 (October is partial: 12 days).

Markets in the cell's universe: 45,554; dropped as unresolved or invalid: 1; with no taker-BUY print in the window: 34,977. Taker-BUY prints in the window, all prices: 23,628; in the registered bucket [0.03,0.10): 894 on 346 markets.

Fill model as registered (R-0946): realistic = the maker was the other side of the print at its price p (deciding); conservative = fill one tick worse, q = p − 0.01, which is the trade-through reading (a resting sell at q is filled only by a later taker-BUY print strictly above it). Metric r = (q − payout)/(1 − q) per complement dollar; no maker fee (docs: "Makers are never charged fees"); rebate rows use today's schedule: crypto taker fee 0.07 × p(1 − p) per share, maker rebate 20% of taker fees (https://docs.polymarket.com/trading/fees.md, https://docs.polymarket.com/programs/maker-rebates.md), applied per fill as an upper bound. Cluster bootstrap by event and by UTC day (10,000 resamples, wider interval shown), as registered; the by-market interval is added.

| row | fills | markets | events | losing events (prints) | r_maker | 90% CI (cluster) | size-wtd | event-wtd | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | set rule |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| realistic, rebate 0 (registered headline) | 894 | 346 | 329 | 22 (89) | -0.039 | -0.085 to +0.004 (event) | -0.005 | +0.002 | -0.045 | +0.071 | 0.067 | 0/2 | $1,363 | fail |
| realistic, rebate 20% (today's schedule, upper bound) | 894 | 346 | 329 | 22 (89) | -0.038 | -0.084 to +0.004 (event) | -0.004 | +0.003 | -0.045 | +0.071 | 0.067 | 0/2 | $1,363 | fail |
| conservative, rebate 0 (registered headline) | 894 | 346 | 329 | 22 (89) | -0.049 | -0.095 to -0.006 (event) | -0.015 | -0.008 | -0.056 | +0.060 | 0.067 | 0/2 | $1,371 |  |
| conservative, rebate 20% (today's schedule, upper bound) | 894 | 346 | 329 | 22 (89) | -0.048 | -0.095 to -0.006 (event) | -0.015 | -0.008 | -0.056 | +0.060 | 0.067 | 0/2 | $1,371 | fail |
| realistic, rebate 0, without the hourly strike series (sensitivity) | 765 | 259 | 252 | 18 (79) | -0.044 | -0.097 to +0.004 (event) | -0.006 | -0.003 | -0.039 | +0.071 | 0.077 | 0/2 | $1,159 | fail |
| realistic, rebate 0, hourly strike series only (descriptive) | 129 | 87 | 77 | 4 (10) | -0.008 | -0.094 to +0.065 (event) | +0.007 | +0.018 | -0.082 | — | 0.119 | 1/2 | $203 |  |

By-market 90% CI, realistic, rebate 0: -0.085 to +0.002.

## Sub-buckets inside [0.03,0.10) (realistic, rebate 0)

| price | fills | markets | losing events | r_maker | 90% CI (cluster) |
|---|---|---|---|---|---|
| [0.03,0.05) | 221 | 141 | 8 | -0.049 | -0.119 to +0.014 |
| [0.05,0.07) | 303 | 147 | 10 | -0.020 | -0.072 to +0.024 |
| [0.07,0.10) | 370 | 181 | 16 | -0.048 | -0.113 to +0.011 |

## Per month (realistic, rebate 0)

| month | fills | markets | losing events | r_maker |
|---|---|---|---|---|
| 2026-09 | 732 | 271 | 20 | -0.038 |
| 2026-10 | 162 | 75 | 2 | -0.043 |

## Context: every taker-BUY print in the window, by registered price bucket (realistic, rebate 0; descriptive)

| bucket | fills | losing events | r_maker |
|---|---|---|---|
| [0.01,0.03) | 324 | 6 | +0.001 |
| [0.03,0.10) | 894 | 22 | -0.039 |
| [0.10,0.20) | 1,266 | 63 | +0.007 |
| [0.20,0.35) | 1,773 | 138 | +0.009 |
| [0.35,0.50) | 2,609 | 201 | +0.063 |
| [0.50,0.65) | 4,219 | 262 | -0.157 |
| [0.65,0.80) | 4,739 | 424 | -0.022 |
| [0.80,0.90) | 3,063 | 426 | -0.118 |
| [0.90,0.97) | 2,181 | 450 | -0.030 |
| [0.97,0.99] | 2,560 | 616 | +0.448 |

## Registered C2 reading: January 2026 (census sample, from the pause release) ∪ this set

January contributes 832 prints from the census sample of 2026-01 (a stratified sample with the census's print-walk truncation); this set is the full population of its range. The two sources differ in construction; the pooling is what the registration's C2 definition asks for.

| row | fills | markets | events | losing events (prints) | r_maker | 90% CI (cluster) | size-wtd | event-wtd | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | set rule |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C2 = Jan ∪ stage 3, realistic, rebate 0 | 1,726 | 516 | 492 | 27 (113) | -0.005 | -0.039 to +0.023 (event) | -0.020 | +0.010 | -0.030 | +0.028 | 0.046 | 1/3 | $1,575 | fail |
| C2 = Jan ∪ stage 3, conservative, rebate 0 | 1,726 | 516 | 492 | 27 (113) | -0.016 | -0.049 to +0.012 (event) | -0.030 | -0.001 | -0.040 | +0.017 | 0.046 | 1/3 | $1,583 |  |

Registered verdict with C1 = pass (June–August 2026, from `confirmation_C1.json`), C2 = fail, conservative C1 ∪ C2 = +0.002: **rejected** (C2's October is covered through the 12th only, so the registration's own C2 remains incomplete).

## Reading

**Against the bar: fail.** The set is decisive under the registered per-set rule (894 prints,
22 losing events, both above the 300 / 5 minimums). The maker's return at the taker-BUY
print price is −3.9 cents per complement dollar, the wider cluster 90% interval runs from
−8.5% to +0.4% and does not clear zero, both months are negative, and the conservative
(trade-through, one tick worse) reading is −4.9% with an interval entirely below zero. The
maker rebate as published today (20% of the taker's 0.07 × p(1 − p) fee, applied per fill as
an upper bound) adds a tenth of a point; the registered zero-rebate row is the sensitivity the
task asked for and reads the same. Capacity is not the constraint ($1,363 per month at $1,000
with 10% of flow). Adverse selection is +7.1 points at 30 minutes (the price fell after these
fills, which favours the maker) and −4.5 at 5 minutes; the loss comes from the tokens that
paid out: 89 prints in 22 events, 2.5% of fills against an implied 6% — fewer losses than
calibration, yet the coupon (3–10 cents on a complement costing 90–97) does not cover them.

**Against the registered verdict rule: rejected.** C1 passed (June–August 2026: 1,689 prints,
+3.1%). C2 is January ∪ this set under the registration's own definition: 1,726 prints, 27
losing events, −0.5% (−3.9% to +2.3%), fail. The conservative pooled C1 ∪ C2 return is
+0.2%. The verdict is `rejected` in the §7.5 vocabulary; "failed" in the task's words. The
registration lists all of October in C2 and this set covers it through the 12th; the
remaining 19 days would have to add several hundred strongly positive fills to lift the
pooled interval above zero, which nothing in the three sets suggests.

**What the three-set picture now says.** D +5.1% (1,024 prints, 6 losing events), C1 +3.1%
(1,689, 16), January +3.1% (832, 5), September–October −3.9% (894, 22). The sign flipped on
the first set with a full population rather than a census sample and with the most losing
events. The D-and-C1 agreement that made this cell the programme's one open question was
built on 6 and 16 losing events; the cell's return is a coupon minus a tail, and the tail
decides.

**Composition.** 36,090 of the 45,554 in-cell markets are the 2026 hourly strike series
("<coin> above <strike> on <date>, <hour> ET?"); they produce 129 of the 894 fills and
contribute 4 losing events, −0.8% on their own (underpowered). Without them the cell is 765
fills on 259 daily/weekly ladders at −4.4% (−9.7% to +0.4%), the same fail. The series is left
inside the cell because the registered `ultra_short` rule, applied by the frozen classifier,
leaves it there (docs/DATA_NOTES.md, "S1b stage 3").

**Not done here.** The other four S1b candidates were not re-run on this set (the task named
one cell; their September/October data is a new registration). No paper or live trading was
started or configured. The `s1b_by` question (R-0947) still waits for October's full month.

## Files

- `data/s1b-stage3/manifest.json` (compressed copy committed as `reports/s1b-stage3-manifest.json.gz`): the range, the registered definitions verbatim, today's fee schedule with its pages, the data-availability notes and the 45,554 in-cell markets.
- `data/s1b-stage3/{markets,prints,bars,resolutions,fills}.parquet`, `results.json`, `fetch_summary_{daily,hourly}.json`, raw JSON records under `data/s1b-stage3/raw/` (110,077 requests, 2 retries, 0 failures). Not committed (data/ is outside git); regenerable with `tools/research/s1b_stage3.py manifest|fetch|replay`.
- `tools/research/s1b_stage3.py`: the measurement, reusing `cell_stats`, `set_rule`, `derive` and the cluster bootstrap from the registered S1b tool.
