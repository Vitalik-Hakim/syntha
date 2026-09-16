# S1b — resting-order return at taker-BUY prints (maker side of S1)

**Status (2026-09-16): discovery on D done; all five rule-passers run on C1 and on the
available C2 month (2026-01) under the registered confirmation criteria (Amendment 1,
R-0946). Provisional verdicts until 2026-09 joins C2: one rejected, four underpowered. No
cell is confirmed.** Registered as R-0769 (study) and R-0770…R-0945 (176 primary cells)
before the run; `registration.yaml` records that the taker census on D motivated the family.

## Confirmation (C1: 2026-06…08; C2: 2026-01; realistic = fill at the print price, conservative = one tick worse)

| Cell | D | C1 (n, loss events, r, 90% CI) | C2 (n, loss events, r, 90% CI) | conservative C1 ∪ C2 | verdict |
|---|---|---|---|---|---|
| politics [0.20,0.35) −6h | +0.229, 23 loss events | 55 prints, 5, −0.181 (−0.59 to +0.31) | 102 prints, 4, +0.166 (−0.14 to +0.38) | +0.030 | **underpowered** |
| crypto [0.03,0.10) −15m | +0.051, 6 | 1,689, 16, +0.031 (+0.008 to +0.048) **pass** | 832, 5, +0.031 (−0.011 to +0.058) **fail** | +0.020 | **rejected** |
| politics [0.03,0.10) −1h | +0.046, 7 | 70, 1, +0.034 (−0.03 to +0.07) | 151, 0, +0.061 (+0.05 to +0.08) | +0.042 | **underpowered** |
| weather [0.03,0.10) −1h | +0.039, 12 | 198, 4, +0.036 (+0.005 to +0.059) | 67, 3, +0.018 (−0.03 to +0.06) | +0.020 | **underpowered** |
| politics [0.01,0.03) −1h | +0.020, 0 | 27, 0, +0.020 (+0.017 to +0.023) | 172, 0, +0.017 (+0.016 to +0.020) | +0.008 | **underpowered** |

Every verdict is provisional: C2 has one month until 2026-09 resolves (`report/verdicts.md`
says so on every row). Reading:

- **The candidate did not survive contact with 2026.** politics [0.20,0.35) −6h has 55 prints on
  C1 (19 events) at −18%, size-weighted −94% (one large losing print), and 102 prints on C2 at
  +17% with four losing events. Neither set is decisive; the D effect (+23% on 674 prints) is
  not contradicted, but nothing out of sample supports it either. The registration's
  "confirmation" was the same measurement on months where the cell holds a few dozen prints.
- **crypto [0.03,0.10) −15m** is the only cell with power out of sample: C1 passes (1,689
  prints, 16 losing events, +3.1%, CI above zero), C2 fails (832 prints, 5 losing events, the
  interval spans zero), and the conservative preset is +2.0% with its C1 interval touching
  zero. Under the registered rule that is a rejection; on the numbers it is a +2–3% maker
  edge that one tick of queue position eats. AS 30m is +0.03 on both sets: the price falls
  after these fills, the maker is not picked off.
- **The three thin cells** have 0–4 losing events on both sets: the "no tail observed" flag
  the registration template now carries is exactly what confirmation showed.
- The 2026 census months have far fewer non-ultra_short taker-BUY prints than 2025 H2 (C1:
  203,555 outside ultra_short in three months; D's ten months: 334,342) — the census's print
  walk truncation (docs/DATA_NOTES.md, "Census months") thins the confirmation sets.

## Splits on D (report regenerated with event-weighted returns, year and deadline-type splits)

| Cell | event-weighted | 2025 H2 (n) | 2026 (n) | BY (n) | ON (n) | NONE (n) |
|---|---|---|---|---|---|---|
| politics [0.20,0.35) −6h | +0.115 | +0.313 (110) | +0.213 (564) | −1.000 (1) | +0.003 (18) | +0.237 (655) |
| crypto [0.03,0.10) −15m | +0.039 | +0.051 (629) | +0.052 (395) | −1.000 (1) | +0.053 (740) | +0.049 (283) |
| politics [0.03,0.10) −1h | +0.012 | +0.070 (415) | +0.021 (401) | +0.086 (80) | −0.330 (11) | +0.047 (725) |
| weather [0.03,0.10) −1h | +0.020 | −0.023 (110) | +0.049 (637) | +0.035 (1) | +0.039 (744) | +0.046 (2) |
| politics [0.01,0.03) −1h | +0.022 | +0.022 (310) | +0.019 (253) | +0.029 (2) | +0.018 (6) | +0.020 (555) |

Pooled primary family: BY +0.047 (1,355 prints), ON +0.005 (41,979), NONE +0.020 (17,797);
2025 H2 +0.004 (25,149), 2026 Feb–May +0.015 (35,982); event-weighted +0.017 overall. The
event-weighted return of the politics candidate (+0.115) is half its print-weighted one: a
few events with many prints carry it.

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

## Next step

Re-run `confirm --set C2 --months 2026-01 2026-09` and `verdict` once 2026-09 is in the
census; the verdicts stop being provisional then. Nothing here is a reason to run anything
else.

## Files

- `report/report.md`, `report/resting.parquet` (660 cells: 176 primary, 484 secondary),
  `report/summary.json`; `report/confirmation_C1.*`, `report/confirmation_C2.*` (both
  presets, splits, losing-event counts), `report/verdicts.*`.
- Per-month fill caches in `data/checkpoints/s1b/<month>.parquet` (disposable).
