# S2 — deadline decay (PRD v3 §5.2)

**Verdict: rejected** (PRD v3 §6.2; `report/verdicts.md`, 2026-09-16). The one registered rule
fails the set rule on D, on C1 and on C2, and the conservative C1 ∪ C2 return is negative.

## What was registered

`registration.yaml` (register rows R-0706 study, R-0707…R-0766 the 60 test-1 contrast cells,
R-0767 the rule, R-0768 its DSL form) froze, before the first census month existed:

- **Test 1** (calibration contrast): at each offset and NO-price bucket, is the NO token's
  realised return on BY markets different from the same cell on ON markets? Computed inside the
  S1 discovery report (`../s1_calibration/report/s2_contrasts.parquet`).
- **Test 2** (the rule, `rule_deadline_decay_no_72h.json`): BUY_NO on `deadline_types: [BY]`
  markets when the NO price is in [0.50, 0.90) with 60–72 hours to the scheduled end and the
  7-day price change is at most 10 points; LIMIT +1 tick, TTL 5 hourly bars; 2% of initial
  capital; hold to settlement. Deciding run = REALISTIC execution with CURRENT fees; the
  CONSERVATIVE preset is the robustness arm. Set rule: 90% cluster CI excludes zero, positive
  in a majority of months, capacity ≥ $50/month at $1,000.

## Test 1 — underpowered, no claim

No contrast cell reached 100 observations in its BY arm on the ten D months (the largest BY
arms are 40–90 observations at −24h and −6h in the 0.50–0.90 NO-price buckets). The
registration asks for n ≥ 500 per arm; nothing is testable, and the cells stay "done, untested"
in the register with their arm sizes as the negative-space record. The BY population is small
because `deadline_type` is derived from the question text (`parse.deadline_type`), and most
2025–2026 markets are ON-type (single dated outcome) or unclassifiable.

## Test 2 — the rule through the engine

Runs on the census collections (`census-<month>`, 60-minute bars, dataset version `8cac86a6…`),
`tools/research/confirm.py`, 2026-09-16 11:02–11:15 UTC:

| Set | Trades | Return/$ (pooled, CURRENT fees) | 90% CI (key cluster) | MDE @80% | months + | Conservative return/$ | capacity $1k/mo | set rule |
|---|---|---|---|---|---|---|---|---|
| D (10 months) | 27 | +0.039 | −0.135 to +0.189 (event) | 0.246 | 6/10 | +0.145 | $34 | fail |
| C1 (2026-06…08) | 9 | +0.296 | −0.195 to +0.727 (day) | 0.698 | 2/3 | −0.003 | $46 | fail |
| C2 (2026-01) | 4 | −0.414 | −1.000 to +0.416 (event) | 0.912 | 0/1 | −0.029 | $67 | fail |

Per month (deciding preset unless marked; capacity at $1,000):

| Set | Month | Signals | Filled | Trades | Return/$ | Capacity | Conservative trades | Conservative return/$ | Deciding run |
|---|---|---|---|---|---|---|---|---|---|
| D | 2025-07 | 1 | 0 | 0 | — | $0 | 0 | — | BT-20260916-00085 |
| D | 2025-08 | 8 | 1 | 1 | +0.403 | $11 | 0 | — | BT-20260916-00087 |
| D | 2025-09 | 11 | 1 | 1 | +0.314 | $8 | 0 | — | BT-20260916-00089 |
| D | 2025-10 | 11 | 4 | 4 | -0.380 | $68 | 2 | -0.441 | BT-20260916-00091 |
| D | 2025-11 | 3 | 0 | 0 | — | $0 | 0 | — | BT-20260916-00093 |
| D | 2025-12 | 21 | 1 | 1 | +0.205 | $20 | 1 | +0.203 | BT-20260916-00095 |
| D | 2026-02 | 23 | 1 | 1 | -1.000 | $8 | 0 | — | BT-20260916-00097 |
| D | 2026-03 | 50 | 10 | 10 | +0.119 | $112 | 4 | +0.427 | BT-20260916-00099 |
| D | 2026-04 | 36 | 5 | 5 | +0.201 | $65 | 1 | +0.126 | BT-20260916-00101 |
| D | 2026-05 | 56 | 4 | 4 | +0.170 | $46 | 0 | — | BT-20260916-00103 |
| C1 | 2026-06 | 31 | 3 | 3 | -0.692 | $43 | 2 | -1.000 | BT-20260916-00105 |
| C1 | 2026-07 | 38 | 3 | 3 | +0.553 | $35 | 1 | +0.247 | BT-20260916-00107 |
| C1 | 2026-08 | 34 | 3 | 3 | +0.869 | $59 | 3 | +0.578 | BT-20260916-00109 |
| C2 | 2026-01 | 18 | 4 | 4 | -0.414 | $67 | 3 | -0.029 | BT-20260916-00111 |

## Reading

- **The rule almost never fires.** 40 trades across 14 months. The universe (BY deadline, NO
  in 0.50–0.90 at exactly 60–72 hours out, flat week) is a few markets a month, and the
  LIMIT-at-touch fill on hourly census bars loses about half of those signals.
- **Nothing is measurable at this size.** The MDE the registration committed to reporting is
  25 points on D and 70–90 points on the confirmation sets. A +4% pooled return on D with a
  CI of ±16 points is a negative result with an MDE, not evidence either way about the
  hypothesis at a plausible effect size (the registration's MDE target was 5 points).
- **Capacity fails on its own** ($34–$67 per month against the ≥ $50 bar on D), so even a
  real effect here would not be tradable at $1,000.
- **Ordering held.** D was run and its table written before C1, C1 before C2; no parameter was
  touched between sets; the rule ran exactly as registered on 2026-09-15.

## Files

- `report/confirmation_D.md` / `.json`, `confirmation_C1.*`, `confirmation_C2.*` — per-set
  tables with every run id (deciding and conservative), pooled returns, cluster CIs by event
  and by day, MDE, capacity.
- `report/verdicts.md` / `.json` — the §6.2 verdict table.
- Run ids BT-20260916-00085 … BT-20260916-00112 in `backtest_runs` (28 runs: 14 months × 2
  presets), all `DONE`.

## What would make this testable

A BY population an order of magnitude larger: either more months (2026-09 and 2026-10 once
resolved, then the months the census has not sampled) or a `deadline_type` classifier that
recovers BY markets the question-text rule misses. Both are new registrations, not a re-run.
