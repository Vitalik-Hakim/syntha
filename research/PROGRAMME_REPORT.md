# Programme report at pause — 2026-09-16 (stage-3 addendum 2026-10-12)

**Read this first.** Every number below comes from the study reports already in this
repository (`research/studies/*/README.md`, their `report/` folders, `docs/RESEARCH_PROTOCOL.md`
and, for the addendum, `reports/s1b-stage3.md`); nothing was recomputed for this document. The
programme is paused after M9 and the S1b measurement, before S3, S4 and S5. The confirmation
sets were incomplete at the pause: C2 held January 2026 only. On 2026-10-12 the one S1b cell
with a C1 pass was confirmed out of sample on the full September–October 2026 crypto universe
(stage 3) and rejected; the other S1b cells remain provisional. No result here is a trading
result. The recorder's frames and the raw Polymarket record on this machine were deleted at
the pause (docs/RESUME.md); the study reports, the register, the exports attached to the
`pause-2026-09-16` release, and the code are what remain.

The question (PRD v3 §1.1): *is there any edge that a small, rule-following account can
capture on Polymarket — and if so, where, how large, and with how much capital?*

## 1. Verdicts (PRD v3 §7.5 vocabulary), with register N

The register (`docs/research/REGISTER.md`) has 947 rows: 44 rule configurations from the
v2 strategy search (M7, retro-registered), 661 for S1, 63 for S2, 179 for S1b (including the
confirmation criteria and `s1b_by`). Each row is a hypothesis that was written down before it
ran, or is labelled as retro-registered or registered at pause.

| Study | What was tested | Register N | Verdict |
|---|---|---|---|
| M7 strategy search (v2) | 44 rule configurations in seven families on 1-minute mids of ~2,800 liquid markets, seven months | 44 | **rejected** (one "promising / unconfirmed" family returned to unconfirmed on its stage-3 months; read plainly, no edge) |
| S1 executable calibration surface | 660 cells (11 categories × 10 fill-price buckets × 6 offsets) of taker return on 1,851,402 census observations, ten discovery months | 661 | **no candidate** (no cell cleared the pre-registered rule; no confirmation run because there was nothing to confirm) |
| S2 deadline decay | test 1: BY-vs-ON calibration contrasts (60 cells); test 2: one dynamic rule through the engine | 63 | test 1 **underpowered** (no BY arm reached 100 observations); test 2 **rejected** on D, C1 and C2 |
| S1b resting-order return (maker side of S1) | 176 primary cells of the maker's return at taker-BUY prints; five D rule-passers confirmed on C1 and C2 (January); the crypto cell then on September–October 2026 | 179 | crypto [0.03,0.10) −15m **rejected** (stage 3); four cells **underpowered**, provisional |

Nothing is confirmed. Nothing is promising / unconfirmed.

## 2. The map: magnitudes, intervals, and what could have been missed

**Taker side (S1).** 1,851,402 token-observations from ten census months; 94,254 carried an
executable fill (a print at or below mid + 1 tick within 10 minutes). The map is mostly
negative space:

- Only 3 of 660 cells reached 500 filled observations and 9 reached 300, all of them sports.
  Their taker returns under CURRENT fees run from −21% to +3% per dollar with 90% intervals that
  span zero in 7 of 9; the two that do not are negative (sports [0.50,0.65) at −7d: −8.4%;
  sports [0.20,0.35) at −15m: −21.4%). Their MDE@80% is 8 to 22 points: an effect smaller than
  that could not have been seen in them.
- 184 cells have 30–299 observations; their median MDE is 25 points. 467 cells have fewer than
  30. The census fill rate outside the last hour is 1–3%, so most of the surface is unmeasured
  at the executable price, not measured and flat.
- Pooled by offset (cells with ≥ 30 fills): taker returns of −7.5% at −15m, −10.5% at −1h,
  −0.2% at −6h, −3.1% at −24h, −1.4% at −3d, −3.1% at −7d. In the four lowest price buckets
  within 24 hours of the deadline the pooled taker return is −10.7% on 7,685 fills, while the
  mid is only 1.8 points above the realised payout: takers overpay at low prices near the
  deadline, mostly by the fee.
- One pass-1 candidate (crypto [0.35,0.50) at −15m, +28%) was an artefact: the 5/15-minute
  "Up or Down" series listed days ahead, plus a fill model that averages a fast market's dips.
  It is documented in full (`research/studies/s1_calibration/README.md`) and excluded by
  classification; the regression fixtures keep it excluded.

**Deadline stratification (S2).** BY-deadline markets are too rare in the census for a
contrast: 56 of 60 contrast cells had both arms, none with 100 BY observations against the
500 the registration required. The registered rule (BUY NO at 60–72 hours before a BY deadline
on a flat week) fired 40 times in fourteen months: +3.9% on D (CI −13.5% to +18.9%, MDE 25
points), +29.6% on C1 (9 trades), −41% on C2 (4 trades), capacity $34–$67 per month against
the $50 bar. Rejected, and the MDE says the test could not have seen an effect under 25 points.

**Maker side (S1b).** 10.3 million taker-BUY prints in the census fill windows on D, 97% of
them on the ultra-short crypto series and excluded; 61,131 in the primary family (price below
0.35, within 24 hours of the deadline). The family is flat: +1.0% per complement dollar
equal-weight, 0.0% size-weighted, +1.7% event-weighted, adverse selection about −0.6 points at
5 and 30 minutes. By deadline type: BY +4.7% (1,355 prints), ON +0.5% (41,979), NONE +2.0%
(17,797). By year: 2025 H2 +0.4%, 2026 Feb–May +1.5%. The 25%-of-taker-fee rebate moves the
family by +0.3 points.

Five cells passed the registered D rule. The confirmation (C1 = June–August 2026; C2 =
January 2026 at the pause, September–October 2026 added by stage 3 for the crypto cell),
realistic preset = fill at the print price, conservative = one tick worse:

| Cell | D (n, losing events, return) | C1 | C2 (Jan) | conservative C1 ∪ C2 | verdict |
|---|---|---|---|---|---|
| politics [0.20,0.35) −6h | 674, 23, +22.9% (CI +12% to +31%) | 55, 5, −18.1% | 102, 4, +16.6% | +3.0% | underpowered (provisional) |
| crypto [0.03,0.10) −15m | 1,024, 6, +5.1% (CI +3.7% to +6.2%) | 1,689, 16, +3.1% (CI +0.8% to +4.8%): pass | 832, 5, +3.1% (CI −1.1% to +5.8%): fail; with Sep–Oct 2026: 1,726, 27, −0.5% (CI −3.9% to +2.3%): fail | +0.2% | **rejected** |
| politics [0.03,0.10) −1h | 816, 7, +4.6% | 70, 1, +3.4% | 151, 0, +6.1% | +4.2% | underpowered (provisional) |
| weather [0.03,0.10) −1h | 747, 12, +3.9% | 198, 4, +3.6% | 67, 3, +1.8% | +2.0% | underpowered (provisional) |
| politics [0.01,0.03) −1h | 563, 0, +2.0% | 27, 0, +2.0% | 172, 0, +1.7% | +0.8% | underpowered (provisional) |

The crypto cell was the one place in the programme where three separate sets agreed in sign:
+5.1%, +3.1%, +3.1% per complement dollar with 6, 16 and 5 losing events. Stage 3
(2026-10-12, `reports/s1b-stage3.md`) measured it on every resolved crypto market with a
scheduled end from 2026-09-01 to 2026-10-12, with the cell, fill model, metric, bar and
bootstrap unchanged: 894 fills on 346 markets, 22 losing events, −3.9% per complement dollar
(CI −8.5% to +0.4%), both months negative, conservative −4.9% (interval below zero), the
published 20% maker rebate worth a tenth of a point. The sign flipped on the first set drawn
from a full population rather than a census sample, which was also the set with the most
losing events. Pooled with January as the registration's C2 definition requires, C2 fails and
the verdict is rejected. Its capacity figures ($332, $1,161, $2,001 and $1,363 per month at
$1,000 with 10% of flow across the four sets) were the largest the programme produced, and are
moot. The politics [0.01,0.03) cell has no losing event on any set: its intervals are the size
of the coupon, not evidence, and that failure of the interval is now a criterion in the
registration template (`min_losing_events: 5`).

## 3. Wallet decay curve and structural episodes

Not measured. S3 (wallet skill persistence) has a draft registration
(`research/studies/s3_wallets/registration.yaml`), not registered and not run. S4 (structural
violations, paper scanner) was not started. There is no decay curve and no executability
distribution to report.

## 4. What the recorder captured versus what the backtests expected

No paper month exists. The recorder was restarted on 2026-09-16 with the stratified watchlist
and a rolling final-24h politics slot and ran from 17:20 to 21:01 UTC; one hour of rows was
committed (a disk-guard bug, fixed the same evening, rolled back the rest), and the coverage
window is recorded in `docs/DATA_NOTES.md` ("Recorder stopped at pause"). Its frames were
deleted at the pause. S5 (maker simulation) requires 30 days of recording; the clock restarts
from zero on resume.

## 5. Everything tried, from the register

947 rows. The M7 configurations (R-0001 … R-0044) are listed with their holdout results in
`docs/RESEARCH_PROTOCOL.md`. The S1 cells (R-0046 … R-0705) each carry their n, return, 90%
interval and MDE from the pass-3 report. The S2 contrast cells (R-0707 … R-0766) carry their
arm sizes; its rule (R-0767) and DSL (R-0768) carry the three-set result. The S1b cells
(R-0770 … R-0945) carry their D outcome, the five candidates their C1/C2 outcome, R-0946 the
confirmation criteria and the stage-3 result, and R-0947 the `s1b_by` question registered at
pause. Rows paused at the pause, with what they wait for, are marked `paused` and dated
2026-09-16; R-0793 and R-0946 were closed by stage 3 on 2026-10-12.

## 6. The plain-language answer

Over sixteen months of Polymarket data — seven months of 1-minute mids on liquid markets,
fourteen census months of 146,000 sampled markets, and six weeks of the full crypto universe —
nothing clears the bar for "confirmed": no rule, no calibration cell, no deadline effect, and
no maker cell has a cluster-robust 90% interval above zero on two disjoint confirmation sets.
The taker side is well calibrated net of costs where it can be measured, and mostly
unmeasurable at the executable price elsewhere, with minimum detectable effects of 8 to 25
points. The strongest maker candidate — resting orders against small crypto taker buys in the
last quarter-hour — returned two to five cents per complement dollar in three sets and lost
four cents in the fourth, the first drawn from a full population; the loss comes from the few
tokens that pay out, and the coupon does not cover them. The v3 prior that liquidity provision
would be the most likely positive expectation, and the smallest per unit of capital, is not
contradicted by what was measured, and is not supported by it either.

## Paused before S3, S4 and S5

- **S3 wallet persistence**: draft registration exists; Phase A needs the wallet-side
  classification and nulls it specifies; not registered, not run.
- **S4 structural violations**: not started; needs D2 event-complete ingest and D4 question
  parsing beyond the deadline-type subset that exists.
- **S5 maker simulation**: needs 30 days of E7 recording; the recorder ran for hours before
  the pause and its frames are not kept, so the requirement restarts from zero.
- **S6 Kalshi**: optional; not started.
- **Pending clocks**: October 2026 (the full month) is the only untouched month for the
  BY-deadline maker question raised by the S1b split (`s1b_by`, R-0947, registered paused at
  the pause from a post-hoc split, so it is D-informed and can only be tested there); the
  recorder from zero. September and October are new registrations on resume
  (`docs/RESEARCH_PROTOCOL.md`); stage 3 used them for one pre-registered cell only.
