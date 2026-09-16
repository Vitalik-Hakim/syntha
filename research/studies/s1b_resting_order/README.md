# S1b — resting-order return at taker-BUY prints (maker side of S1)

**Status: discovery on D done (2026-09-16); five cells pass the registered rule, one of them
with no loss event at all; the family as a whole is flat. No C1/C2 run has been made — the run
stopped after D as instructed.** Registered as R-0769 (study) and R-0770…R-0945 (176 primary
cells) before the run; `registration.yaml` records that the taker census on D motivated the
family, so D is its discovery set only.

## What was measured

Every taker-BUY print inside a census observation's 10-minute fill window on the ten D months
(`tools/research/s1b_resting_order.py`, report in `report/`). The maker who was the other
side of the print sold the token at p and holds the complement to resolution:
r_maker = (p − payout) / (1 − p), no taker fee, rebate 0 (headline) and 25% of the CURRENT
taker fee (sensitivity). Adverse selection = the position marked 5 and 30 minutes after the
fill (1-minute bar where one exists — none exist for the 2025 census tokens, 4.7–27M bars
per month in 2026 — else the next print of any side within 24 hours). Primary family: print
price below 0.35 at the four offsets within 24 hours of the scheduled end; everything else
secondary and reported.

| | prints |
|---|---|
| Taker-BUY prints in fill windows, D | 10,323,298 |
| … on ultra_short markets (5/15/60-minute crypto series; excluded from every cell) | 9,988,956 (97%) |
| … in non-ultra_short markets | 334,342 |
| … in the primary family (p < 0.35, offsets ≤ 24h) | 61,131 |

The taker flow the census sees is overwhelmingly the short crypto series. What is left for
the primary family is 61,131 prints in 128 populated cells (48 of 176 have fewer than 30).

## Result

**The family is flat.** Pooled over the primary family: r_maker +0.010 equal-weight, +0.000
size-weighted, +0.013 with the 25% rebate; adverse selection −0.006 at 5 minutes and −0.007
at 30. By bucket +0.001 / +0.014 / +0.013 / +0.009 (lowest to highest); by offset +0.020 (−24h),
+0.002 (−6h), +0.006 (−1h), +0.018 (−15m). The S1 taker's −10.7% per dollar in the same
cells did not become the maker's gain: it is mostly the CURRENT fee, and the maker's return is
measured per complement dollar (1 − p ≥ 0.65), so even the fee-free part of the taker's loss
is a few tenths of a point per dollar to the maker.

**Five cells clear the registered rule** (wider cluster 90% CI above zero, positive in ≥ 7 of
10 months, capacity ≥ $50/month at $1,000 with 10% of flow, n ≥ 500 from ≥ 30 events):

| # | Cell | n | events | loss events (token won) | r_maker | 90% CI | AS 30m | capacity/mo | read |
|---|---|---|---|---|---|---|---|---|---|
| 1 | politics [0.20,0.35) −6h | 674 | 100 | 23 (67 prints; 9.9% won vs 26.8% implied) | +0.229 | +0.120 to +0.308 (day) | −0.003 | $133 | the only cell with a tail; candidate |
| 2 | crypto [0.03,0.10) −15m | 1,024 | 276 | 6 (14 prints; 1.4% vs 6.1%) | +0.051 | +0.037 to +0.062 | +0.049 | $332 | six loss events carry the interval |
| 3 | politics [0.03,0.10) −1h | 816 | 97 | 7 (15 prints; 1.8% vs 6.1%) | +0.046 | +0.023 to +0.062 | +0.015 | $184 | seven loss events |
| 4 | weather [0.03,0.10) −1h | 747 | 281 | 12 (17 prints; 2.3% vs 5.9%) | +0.039 | +0.024 to +0.051 (day) | +0.010 | $192 | twelve loss events |
| 5 | politics [0.01,0.03) −1h | 563 | 66 | **0** | +0.020 | +0.019 to +0.022 | +0.010 | $149 | **no tail observed**: every token lost, the interval is the coupon |

The "loss events" column is the number of distinct events in which the token paid out — the
only way a maker loses here (r = −1). A cluster bootstrap resamples events; with zero or a
handful of losing events it has nothing to resample on the downside and its interval is the
size of the coupon p/(1 − p), not a statement about the tail. Cell 5 is the extreme case:
563 prints on 66 events at p ≈ 0.02, none paid out. At calibration one expects 66 × 0.02 ≈ 1.3
losing events; seeing none has probability about 0.26. The registered rule passes it; the
report flags it ("no tail observed") and it should not be read as a candidate. Cells 2–4 are
between: real but resting on 6–12 losing events each, and their intervals are optimistic for
the same reason. The report's second pass (`report/report.md`) carries `wins (events)` and
the flag on every cell.

Cell 1 is different in kind: 674 prints across 100 events with 23 losing events, +23 points
per complement dollar, positive in 9 of 10 months, adverse selection zero (the price did not
move against the maker in the next 30 minutes), and it is exactly the S1 taker cell with the
largest loss (politics [0.20,0.35) near the deadline). It is a candidate for C1 and C2. Its
capacity is $133/month at $1,000 with 10% of flow.

## What this does and does not say

- It is in-sample on the set that motivated the family. The registration says so; the
  confirmation sets are untouched.
- The maker is counterfactual: at the front of the queue, always filled when a taker trades
  at that price, no inventory limit, no liquidity-rewards payments. A resting order that is
  not first in queue captures a fraction of these prints; S5 (recorded books) is where queue
  position can be measured.
- The rebate sensitivity moves nothing (+0.3 points on the family); rebates are not the story.
- ultra_short markets: 9.99M prints, r_maker −0.032 equal-weight, adverse selection −0.198 at
  30 minutes — the maker on the 5/15-minute crypto series is picked off. Reported, never pooled.

## Next step (not taken)

C1 (2026-06…08) and C2 (2026-01 now; 2026-09/10 when resolved) with the same measurement,
per the registration's confirmation rule, for cell 1 and — with their loss-event counts
stated — cells 2–4. That is a decision to make, not a run to start.

## Files

- `report/report.md`, `report/resting.parquet` (660 cells: 176 primary, 484 secondary),
  `report/summary.json`.
- Per-month fill caches in `data/checkpoints/s1b/<month>.parquet` (disposable).
