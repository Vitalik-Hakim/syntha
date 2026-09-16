# Strategy search protocol (M7, 2026-09-14)

Written before any configuration below was run on the discovery months. The point of writing it
first: a search over many configurations always produces a "winner", and only a rule fixed in
advance, plus honest accounting of how many things were tried, can tell a winner from luck.

## Data

| Collection | Months | Role | Seen before this protocol? |
| --- | --- | --- | --- |
| `march-2026-liquid-400` | Mar 2026 | discovery | no |
| `april-2026-liquid-400` | Apr 2026 | discovery | no |
| `may-2026-liquid-400` | May 2026 | discovery | no |
| `june-2026-liquid-400` | Jun 2026 | holdout | 7 hand-written candidates were run on it once (M6) |
| `july-2026-liquid-400` | Jul 2026 | holdout | the same 7 candidates, and the July calibration table |

All five are seeded stratified samples of 400 markets across fee categories, pre-filtered on
final Gamma volume ≥ $7,500 (so every candidate uses `min_volume_asof ≥ 10,000`, which keeps
them survivorship-free; see docs/DATA_NOTES.md). Discovery precedes holdout in time: the search
is done on March–May and judged on June–July, which is the order a real strategy would face.

Disclosure: June and July were already used once, to validate the seven M6 candidates. The seven
are included in the grid unchanged, so nothing about them was tuned after that look. Every other
configuration is new, with one exception: to test the sweep tooling before the new months
arrived, three grid configurations (`fav_hold_p0.9_h24`, `fade_l15_d0.15`, `vwap_g0.02_l60`) were
run once on June and July at half strength. All three were negative (pooled −3.2%, −3.8%, −6.9%).
Nothing in the grid was changed afterwards; the run is kept in `data/research/dryrun`.

## Candidates

`tools/research/candidates.py` defines the grid: seven families, each a hypothesis about
prediction-market behaviour, with a coarse grid of 2–9 settings. All use the `EITHER` universe
(both outcomes of a market are candidates; the entry rules pick the side), $1,000, the Realistic
preset, seed 1, and the built-in honesty tests with 100 permutations and 30 random baselines.

| Family | Hypothesis | Settings |
| --- | --- | --- |
| Favourite to resolution | near-certain outcomes are underpriced late (favourite–longshot bias) | 9 |
| Longshot to resolution | the opposite | 3 |
| Late drift to resolution | late information is under-reacted to | 6 |
| Overreaction fade | sharp moves on heavy volume overshoot | 6 |
| Momentum breakout | sharp moves on heavy volume continue | 3 |
| VWAP reversion | the sampled price reverts to where trades actually happened | 4 |
| Early favourite | the favourite is underpriced in a market's first day | 2 |
| M6 candidates (unchanged) | — | 7 |

40 configurations. No configuration is added or changed after the discovery run starts; if the
grid is extended later, the extension is a new sweep with its own accounting.

## Measures

For each configuration, pooled over the discovery months:

- **Pooled ROI** = Σ net P&L / (months × $1,000), with a bootstrap 90% CI over all pooled trades.
- **Months positive**, out of 3.
- **Median permutation percentile** (same entry times, other eligible markets of the same
  category: does *which* market it picked matter?).
- Random-entry percentile, buy-and-hold on the same universe, cost ratio, max drawdown.

## Selection rule

A configuration goes to the holdout only if **all** of these hold on discovery:

1. pooled 90% CI entirely above zero;
2. at least 30 pooled trades;
3. positive in at least 2 of the 3 months;
4. median permutation percentile ≥ 80.

At most the top 5 by pooled ROI. If none qualifies, the answer is "nothing found", and that is
reported as such.

## Multiple-comparison accounting (PRD §15.4)

Every configuration's permutation variants are its own null distribution. Drawing one pooled
null ROI per configuration and taking the maximum, repeated 4,000 times, gives the distribution of
"the best of N by chance". The report states N, the expected best-by-chance ROI, and how often
chance alone beats the actual best. A finalist that does not clear this bar is labelled as such.

## Holdout verdict

Each finalist runs once on June and once on July, unchanged, and also under the Conservative
preset. Verdicts:

- **Confirmed:** pooled holdout 90% CI above zero and both months positive under Realistic.
- **Promising:** pooled holdout ROI positive, but the CI spans zero or one month is negative.
- **Rejected:** otherwise.

Confirmed strategies are added to the app's examples with their full record. Promising ones are
added with the record and the word "unconfirmed" in the description. Rejected ones are reported
and not added.

## What this cannot show

Five months of 400 markets is still a small sample of a market that changes month to month
(buy-and-hold on the YES side swung from −0.7% to +24.7% between two adjacent months). A confirmed
strategy here is evidence worth paper-trading, not a reason to risk money.

---

## Stage 1 outcome (written 2026-09-14 17:55, before any holdout run)

The discovery sweep ran all 40 configurations on March, April and May (0 failures; table in
`data/research/discovery/table.md`). **No configuration met the pre-registered rule.** The
nearest:

- *Longshot to Resolution 0.10–0.20 within 24h*: pooled +10.3%, positive in 3/3 months,
  median permutation percentile 92, but the bootstrap 90% CI is −6.7% to +30.2% (95 lumpy trades)
  and the best of 40 by chance is expected at +4.9% with a 95th percentile of +10.3%: the actual
  best is beaten by chance 5% of the time.
- *Early Favourite ≥0.9 to Resolution*: pooled +0.6%, CI +0.5% to +0.8%, 3/3 months, but only 30
  trades and a median permutation percentile of 73.

One unplanned, coherent finding: all six *Late Drift* configurations (buy the side that rose in
the last day, hold to settlement) lost 7.5–13.3% with 0/3 months positive, and the profitable
configurations are their mirror (cheap tokens near the end paid out more than priced), which
also matches the July calibration table (docs/DATA_NOTES.md).

## Stage 2 (exploratory; declared before running)

Because stage 1 selected nothing, everything below is exploratory: the configurations were chosen
after seeing they failed the rule, so their prior is weaker, and every result is reported, not
just the good ones. The holdout months are still unseen by every configuration below except as
disclosed above.

1. **Mirror grid** (`candidates.grid2`, 4 settings): *Late Fade to Resolution* — buy either
   outcome priced 0.10–0.45 that fell ≥ 5% or ≥ 10% over the last 60 or 240 minutes within 24 h
   of the scheduled end; hold to settlement. Run on March–May first (expected to look good
   in-sample, since it is the mirror of a loss seen there), then on June–July, which is the only
   test that counts. Its own best-of-4 accounting.
2. **Exploratory holdout set**, fixed now: the stage-1 top five by pooled ROI plus the one
   configuration whose CI cleared zero:
   `long_hold_p0.1-0.2_h24`, `favourite_to_resolution_yes`, `long_hold_p0.03-0.1_h24`,
   `fav_hold_p0.95_h6`, `longshot_to_resolution_no`, `early_fav_p0.9`.
   Run once each on June and July, Realistic and Conservative, 200 permutations.
3. Verdicts use the stage-1 definitions (confirmed / promising / rejected), with "exploratory"
   attached to every one of them. Nothing from stage 2 is added to the app's examples as anything
   stronger than "unconfirmed".

## Stage 2 outcome (written 2026-09-14 18:25, after the holdout runs; tables in `data/research/holdout*/table.md`)

**Exploratory holdout set** (June + July, Realistic, $1,000; 6 configurations, so the best of 6
by chance is expected at +7.4% pooled, 95th percentile +17.5%):

| Configuration | Pooled ROI | 90% CI | Jun | Jul | Perm | Random | Conservative | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Longshot to Resolution 0.10–0.20 within 24h | **+24.4%** | +4.0% to +48.0% | +41.8% | +7.0% | 98% | 99% | +15.5% (2/2, CI −0.6% to +33.2%) | meets "confirmed" numerically; **exploratory**, so recorded as *promising / unconfirmed* |
| Favourite to Resolution (YES) [M6] | −0.1% | −5.4% to +3.7% | +3.0% | −3.2% | 91% | 40% | −1.0% | rejected |
| Early Favourite ≥0.9 to Resolution | −0.3% | −2.4% to +1.3% | +0.1% | −0.7% | 98% | 47% | −0.6% | rejected |
| Favourite to Resolution ≥0.95 within 6h | −1.0% | −3.2% to +0.9% | −0.1% | −1.8% | 78% | 32% | −1.1% | rejected |
| Longshot to Resolution 0.03–0.10 within 24h | −2.0% | −8.5% to +6.1% | +5.2% | −9.3% | 36% | 50% | +16.8% (1/2) | rejected |
| Longshot to Resolution (NO) [M6] | −4.2% | −6.3% to −1.9% | −6.0% | −2.4% | 4% | 0% | −6.7% | rejected |

The actual best (+24.4%) is beaten by the best-of-6 null 1% of the time.

**Mirror grid** (Late Fade to Resolution, 4 settings): in-sample on March–May the best setting
made +12.9% with 3/3 months positive, exactly as a mirror of an in-sample loss must. On the holdout
the best made +3.7% (June +14.2%, July −6.9%), CI −8.5% to +17.0%, beaten by chance 42% of the
time, and all four were negative under Conservative. **Rejected.** A hypothesis read off the
discovery data did not transfer; the longshot rule, which was in the original grid, did.

**Status of the one survivor.** *Buy either outcome priced 0.10–0.20 within 24 hours of the
scheduled end, hold to settlement, 2% of starting capital per trade* was positive in all five
months (Mar +8.0%, Apr +3.5%, May +19.5%, Jun +41.8%, Jul +7.0%) and beat its permuted and
random variants in every month. It was also the best of 40 on discovery, and the discovery
result alone was consistent with chance at the 5% level; the holdout is what makes it
interesting. It stays labelled *unconfirmed* because it came through the exploratory branch of
this protocol, its P&L is lumpy (most trades lose everything, a few pay 5–10×), and five months
of one period cannot show that the pattern persists. The next test is written below, in advance.

## Stage 3 (pre-registered, not yet run)

Fetch two further months that no decision above has touched (February 2026 and August 2026 once
its markets have resolved). Run the survivor unchanged, Realistic and Conservative. **Confirmed**
if both months are positive and the pooled four-month holdout CI (Jun, Jul, Feb, Aug) stays above
zero; otherwise it goes back to "unconfirmed". No parameter of the rule may change before that
run. If it is confirmed, the following step is paper trading, not capital.

### Stage 3 launch (2026-09-14 18:46)

Collections `february-2026-liquid-400` (seed 20260921) and `august-2026-liquid-400` (seed
20260922), same sampling and liquid pre-filter as the others, fetched after the stage-2 verdict
was written. The survivor runs with the identical document `long_hold_p0.1-0.2_h24`, seed 1,
$1,000, Realistic and Conservative, 200 permutations. August markets that have not resolved by
the fetch time are valued at their last mark by the engine; their number is reported with the
result, and if it is material the August verdict waits for a re-fetch.

### Stage 3 outcome (2026-09-14 21:00)

Both months fully resolved (August 398/400 binary-resolved, February 397/400). The survivor,
unchanged:

| | Feb | Aug | Stage-3 pooled | Four-month holdout (Jun, Jul, Feb, Aug) |
| --- | --- | --- | --- | --- |
| Realistic | **−29.6%** | **−4.0%** | −16.8% (CI −32.2% to −0.2%) | +3.8% (CI −9.9% to +19.0%), 2/4 months |
| Conservative | −29.3% | −6.5% | −17.9% (CI −25.8% to −8.4%) | −1.2% (CI −11.0% to +9.6%), 2/4 months |

Permutation percentile on the two new months: 19 (Realistic), 3 (Conservative); random-entry
percentile 1 and 0. **Verdict: not confirmed.** Under the rule written in advance it returns to
*unconfirmed*; read plainly, the evidence now favours no edge. Five of seven months positive
sounds good until the sizes are compared: the five gains came from a handful of large payoffs,
and one bad month (February) erased more than all of March, April and July together. This is
what the anatomy warned of ("the next five months can easily be flat"), arriving as a loss
rather than a flat.

What the whole exercise established, with the numbers to back it: over seven months and 2,800
liquid markets, none of 44 rule-based configurations in seven families shows an edge that
survives pre-registration, multiple-comparison accounting and untouched months. The built-in
example stays in the app with this record, labelled as a failed test, because a documented
negative is the most useful thing this search produced.

### Register (M8, PRD v3 §3.6)

From M8 every configuration must have a row in `docs/research/REGISTER.md` before it runs
(`tools/research/register.py`; the sweep and run tools refuse anything else). The 44
configurations above were registered retroactively on 2026-09-14 with the note that all seven
months had already been seen, so their rows document what was tried, not a pre-registration.
Engine 0.7.0 (limit orders, same-side liquidity) also revises every number in this file
downward: the survivor's June run falls from +41.8% to +23.6% with the same document
(docs/DATA_NOTES.md, "M8"). Stage 4 (v3 §6) starts from a clean register and a fresh
discovery set.

## Stage 4 (PRD v3 §6, copied verbatim before M9's discovery command runs)

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

### Registrations

S1 (`research/studies/s1_calibration/registration.yaml`) and S2
(`research/studies/s2_deadline_decay/registration.yaml`) were committed on 2026-09-15 before
the first census month was sampled; their register rows (study, cells, rules) are in
`docs/research/REGISTER.md`. The 2025-07 census is the first data of Stage 4.

### Stage 4 outcome for S1 and S2 (2026-09-16)

The fourteen census months (D: 2025-07…2025-12 and 2026-02…2026-05; C1: 2026-06…2026-08; C2:
2026-01) were sampled, fetched, built bit-identically and verified (docs/DATA_NOTES.md, "Census
months"). The pre-registered S1 discovery command ran on D three times, each time with a
classification fix recorded before any confirmation run: **no candidate survived** once the
5/15/60-minute "Up or Down" series were classified `ultra_short` and excluded, and the one
pass-1 candidate was shown to be a window-VWAP artefact (research/studies/s1_calibration/README.md).
The negative space (660 cells, each with its n, return, 90% cluster CI and MDE) is the S1
result; no C1/C2 run was made because there was nothing to confirm. S2's test 1 was
untestable (no BY arm ≥ 100 observations) and its registered rule, run through the engine as
registered on D, then C1, then C2, is **rejected** under §6.2 (40 trades in 14 months, MDE
25–90 points, capacity below $50/month; research/studies/s2_deadline_decay/README.md). Register
rows R-0045…R-0768 carry the outcomes. 2026-09 and 2026-10 join C1 once their markets resolve;
that is a new registration for whatever is proposed next, not a re-run.

**S1b (registered 2026-09-16 after S1/S2 closed, motivated by the D taker surface and saying
so):** discovery on D only. The maker-side family is flat (+1.0% equal-weight, 0.0%
size-weighted); five cells pass the registered rule, of which one has no loss event and three
rest on 6–12; one (politics [0.20,0.35) −6h) is a candidate for C1/C2. Not run further
(research/studies/s1b_resting_order/README.md). S3 has a draft registration
(research/studies/s3_wallets/registration.yaml), not registered and not run.

### Registration template additions (from S1b, 2026-09-16)

Every registration written after this date carries, in its `selection_rule` and its
`confirmation_rule`, a **minimum-losing-events criterion**:

```yaml
min_losing_events: 5   # distinct cluster-events on the losing side of the estimand
```

A cell (or rule, wallet variant, scan variant) whose losing side has fewer than
`min_losing_events` cluster-events is reported as "no tail observed" with its loss-event count
and is never selected or confirmed, whatever its interval says. Reason: a cluster bootstrap
resamples the clusters it saw; with no (or almost no) losing clusters its interval is the size
of the coupon, not a statement about the tail. S1b's politics [0.01,0.03) −1h cell (563 prints,
66 events, zero paid out, CI +0.019 to +0.022) is the worked example. The criterion is not
applied retroactively to S1b's own selection rule (its report flags such cells instead).
