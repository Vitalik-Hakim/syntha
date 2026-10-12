# Resume notes — paused 2026-09-16 (stage-3 confirmation added 2026-10-12)

Everything on the machine that was not source code or in git was deleted at the pause
(see "Deleted at pause" below). What survives is this repository and the assets attached to
the `pause-2026-09-16` GitHub release. Read this file, then `docs/DATA_NOTES.md`, then
`research/PROGRAMME_REPORT.md`. This file and the programme report were written at the pause
but were not in the pause commit (a `git clean` ran before `git add`); they entered git with
the stage-3 commit of 2026-10-12.

## Current state, in ten lines

1. M0–M8 are merged and CI green; M9 (census, S1, S2) is complete and committed (`c65c59c`).
2. S1 (660 cells, 1.85M census observations, ten discovery months): no candidate; the map is negative space with MDEs of 8–25 points.
3. S2: test 1 untestable (no BY arm ≥ 100), the registered rule rejected on D, C1, C2 through the engine.
4. S1b (maker side, 176 cells): family flat; five D rule-passers run on C1 and C2 (January): one rejected, four underpowered.
5. The register has 947 rows; 5 are `paused` (four S1b candidate cells and R-0947 `s1b_by`); R-0793 and R-0946 were closed by stage 3.
6. S3 has a draft registration (not registered, not run); S4, S5, S6 not started.
7. The recorder ran 2026-09-16 17:20–21:01 UTC; one committed hour of rows; frames deleted; S5's 30-day clock is at zero.
8. The normalized layer, the 14 census months, the liquid-400 collections and every raw file are gone; runs BT-…-00001 onward exist only in the release dump. `data/s1b-stage3/` (1 GB of raw JSON and Parquet from 2026-10-12) exists on the machine that ran stage 3 and is regenerable.
9. The last commit before the pause is `b02a775` (CI green, tagged); the pause commit `060c7b4` and the stage-3 commit follow it.
10. Nothing is confirmed. The S1b crypto cell did not survive September–October (stage 3, 2026-10-12, `reports/s1b-stage3.md`: rejected).

## Verdicts (PRD v3 §7.5 vocabulary)

| Study | Register N | Verdict |
|---|---|---|
| M7 strategy search (v2, 44 configurations) | 44 | rejected |
| S1 executable calibration surface | 661 | no candidate (nothing to confirm) |
| S2 deadline decay — test 1 | 60 | underpowered |
| S2 deadline decay — rule (R-0767/R-0768) | 2 | rejected |
| S1b crypto [0.03,0.10) −15m (R-0793) | 1 | **rejected** — C1 pass, C2 (January ∪ September–October 2026) fail; stage 3 on the full September–October population −3.9% on 894 fills, 22 losing events |
| S1b politics [0.20,0.35) −6h | 1 | underpowered — provisional |
| S1b politics [0.03,0.10) −1h, weather [0.03,0.10) −1h, politics [0.01,0.03) −1h | 3 | underpowered — provisional (0–4 losing events per set) |
| `s1b_by` (R-0947, registered at pause from a post-hoc split) | 1 | not run — paused |

## Pending clocks

- **October 2026 census month** (full month) is `s1b_by`'s only test: the BY-deadline maker
  question came from a post-hoc split of D and has seen C1 and January. Its markets resolve
  through mid-November. The other four S1b cells' September/October data is likewise a new
  registration if anyone wants it.
- **Recorder**: from zero. S5 needs 30 days of E7 recording; none is kept.
- September and October are **new registrations on resume** (docs/RESEARCH_PROTOCOL.md,
  "Pause"): sample, fetch and build each month with the census tooling, write the
  registration, then analyse — in that order. Stage 3 did not build them as census months.

## Pinned versions

| Component | Pinned |
|---|---|
| Python | 3.12 (`uv.lock`: `requires-python == 3.12.*`; CI: `uv python install 3.12`) |
| uv workspace | `uv.lock` frozen; CI runs `uv sync --all-packages --locked` |
| Node / pnpm | Node 22 (CI); `pnpm@12.4.1` (`apps/web/package.json` packageManager); `pnpm-lock.yaml` frozen |
| PostgreSQL / TimescaleDB | image `timescale/timescaledb:latest-pg16` — at pause it resolved to PostgreSQL 16.15, TimescaleDB 2.30.0; pin the digest on resume if reproducibility of the dump matters |
| Redis | `redis:7-alpine` |
| Engine | `ENGINE_VERSION = "0.7.0"` (`packages/engine/src/syntha_engine/__init__.py`) |
| Migration head | `0012_census_first_fill` (`packages/storage/src/syntha_storage/migrations/versions/`) |
| Git | tag `pause-2026-09-16` on `b02a775`; the pause commit on top of it |

## Release assets (`https://github.com/Vitalik-Hakim/syntha/releases/tag/pause-2026-09-16`)

All five were uploaded, downloaded back through the API and checksum-verified before anything
was deleted. Total 173 MB.

| Asset | Bytes | SHA-256 |
|---|---|---|
| `syntha_pause_2026-09-16.dump` — `pg_dump -Fc -Z 9` of backtest_runs, bt_trades, bt_equity, bt_ledger, bt_near_misses, strategies, strategy_versions, dataset_versions, fee_schedules, census_markets, census_runs, catch_all_reviews, recorder_watchlist | 24,027,341 | `cfae4239cb2eeafb4aa92db62f50fd048266542c4114186f9aa47189105e3857` |
| `census_observations.parquet` — all 2,933,486 rows, 17 columns, of `census_observations` (14 months) | 53,640,299 | `b8091ea9b69ff28d90f9874852b67c85d6570c1bc6319f1a5aa9391674d02b6b` |
| `s1b_prints.parquet` — the S1b per-print results: 12,031,385 taker-BUY prints in fill windows with marks, all 14 months, `month` column | 102,306,584 | `b6e40747ad1cd334a8927d2b6ca0eb20ab9380161948175ba177c79e19aadbe2` |
| `REGISTER_2026-09-16.md` — copy of the register at pause (also in git) | 732,118 | `b9a708ad4896100eeb7cdaa0d7c22e308d6084fe3b68dbf908aa5abdb3eefd01` |
| `SHA256SUMS.txt` | 363 | (the manifest itself) |

Restore the dump into a migrated database with `pg_restore -U syntha -d syntha --data-only
--disable-triggers syntha_pause_2026-09-16.dump` after `make up migrate`; the tables it
carries are not rebuilt by `make rebuild-normalized` (which truncates only the normalized
layer). `census_observations` is not in the dump: load it from the parquet, or rebuild the
census months from raw once they are re-fetched.

## Deleted at pause

`data/raw/` (14 GB: Gamma listings and events, CLOB price histories, Data API trades and
resolutions, census manifests, recorder frames), `data/checkpoints/`, the S1b caches, the
Docker volume `syntha_postgres-data` (the normalized layer, the census tables, the recorder
hypertables), the project's images, `.venv`, `node_modules`, `.next*`, caches, `.env`. The raw
record was append-only by rule (CLAUDE.md); it was deleted on the owner's instruction for
this pause, with no external copy.

## Resume sequence

Estimated wall clock at 20 requests/second (the Data API served about 3–4 requests/second to
one address during the census; stage 3 saw about 10 trade walks and 20 price requests per
second with 16-way concurrency; the census driver's measured per-month times are the basis):

1. `cp .env.example .env`, `make setup up migrate` (Docker, uv sync, pnpm install, Alembic to
   `0012`). Restore the dump (above). ~15 minutes.
2. **Listings per month**: `syntha-ingest census-sample` regenerates each month's frame from
   Gamma (keyset paging) and re-fetches its events; the seeded manifests under
   `data/raw/_census/` are gone, so use the same seeds (`YYYYMM01`) — the sampler is
   deterministic given the same listing, but the listing may have changed (markets deleted or
   re-tagged), so the manifests will not be byte-identical. 2026 months list about 33,000
   markets a day (sports ladders); allow an hour per month: ~16 hours for 16 months.
3. **The liquid-400 collections** (february, march, april, may, june, july, august-2026-liquid-400):
   `make ingest-sample` from the seeded parameters in `docs/RESEARCH_PROTOCOL.md` and
   `docs/DATA_NOTES.md`, then `make ingest-fetch` per collection. Trades dominate: roughly
   1.5–2 hours per collection at the Data API's effective rate. ~12 hours.
4. **The 14 census months**: `tools/research/census_months.sh` (sample if no manifest, fetch,
   build twice, verify). Measured on 2026-09-15/16: 30–57 minutes per 2025 month, 1–2.6 hours
   per 2026 month; ~18 hours in all.
5. **September and October 2026** once their markets resolve: ~2–2.5 hours each, then their
   registrations, then the `s1b_by` run on October.
6. `make rebuild-normalized` (truncate-first; needs ~120 GB free): ~2 hours.

Total re-fetch: about 50 hours of wall clock, mostly rate-limited, resumable at every step.

**1-minute retention risk.** The CLOB price-history endpoint returned 1-minute bars only for
recent windows (docs/DATA_NOTES.md: fidelity 1 is served for the last hours of a market and
for the recorder's live markets; older markets return hourly or empty). The liquid-400
collections were built on 1-minute bars fetched while their markets were live or fresh. If
that history is no longer served, those collections cannot be rebuilt as they were, and the
runs **BT-20260914-00001 to BT-20260914-00060** (M6/M7, in the dump) may not be
bit-reproducible: their inputs would differ. The census months use hourly bars in a 15-day
window and 1-minute bars only for the last 6 hours of intraday markets; the hourly part is
retrievable for 2025 markets (verified), the 1-minute part may not be.

**First command on resume**: `git checkout main && git log --oneline -3` to confirm the latest
commit, then `cp .env.example .env && make setup up migrate`.

## Unresolved decisions (PRD v2 §20, still open; PRD v3 adds none)

1. Venue: international Polymarket (default) or the US exchange.
2. Bankroll realism: warn at $100 and suggest $1,000 (default: a non-blocking hint).
3. Multi-outcome events: one position per event (default), configurable.
4. Backtest history retention: keep all `bt_trades` (default); a retention setting later.
5. Self-hosted single-user (default) versus a deployed multi-tenant version.
6. Legal: a research tool, not a trading tool; local advice before any public distribution.

Decisions taken during the programme are in `docs/DECISIONS.md`; the deviations from the
PRD forced by the live API are in `docs/DATA_NOTES.md`. Two open design questions from this
pause: whether a minimum losing-event count belongs in every confirmation rule (it is in the
template for later studies), and whether the `ultra_short` rule should name the 2026 hourly
strike series ("<coin> above <strike> on <date>, <hour> ET?", a 79-minute market the frozen
rule leaves as `intraday`; docs/DATA_NOTES.md, "S1b stage 3").
