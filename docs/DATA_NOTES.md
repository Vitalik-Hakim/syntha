# Data notes

Discrepancies between `docs/PRD.md` and reality (live APIs, database constraints), and factual
assumptions the engine depends on. Per PRD §0 rule 4, reality wins; each entry says what we did.

Status legend: **resolved** (implemented as described), **open** (must be settled in the named
milestone).

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

### `orderbook_snapshots` is not a hypertable — open (M1)

The PRD makes only `price_bars` and `trade_prints` hypertables. The recorder will write L2
snapshots continuously, which is the highest-volume table in the system. Decide when building the
recorder whether to make it a hypertable and whether to store deltas separately (§4.4 mentions
"snapshots and deltas" but the schema only has snapshots).

### `trade_prints` has no natural key, so re-ingestion would duplicate rows — open (M1)

§4.6 requires idempotent ingestion, but a `bigserial` id can't deduplicate a re-fetched print.
M1 must add a natural unique key from the Data API response (likely transaction hash + log
index / asset, to be confirmed against the live payload), including `ts` because of the
hypertable rule above.

### Maker vs taker minimum order size — open (M1)

§3.6 says to store both minimums; §7 has only `min_order_size`. Check which fields Gamma/CLOB
actually return and add a column if a separate maker minimum exists.

### Table names in PRD §0 — noted

§0 rule 3 refers to `trades` and `equity_curve`; the schema names them `bt_trades` and
`bt_equity`. The schema names are used.

---

## To verify before M1 (PRD Appendix A)

Not yet done. Before any ingestion code:

- [ ] Every endpoint in §4.1 against `https://docs.polymarket.com/llms.txt` and a live call
      (params, response shape, pagination, rate-limit headers, `retry_after_seconds`).
- [ ] Whether `prices-history` with `fidelity=1` really returns 1-minute data for old, resolved
      markets, and what the maximum window per request is.
- [ ] Data API `/trades` pagination depth limits (can we reach a market's full print history?).
- [ ] Current fee schedule and its full history of effective dates (help.polymarket.com trading
      fees article; docs.polymarket.us/fees) → seed `fee_schedules` with `source_url`.
- [ ] Settlement charges no fee on winnings (§10.4) — find a source and record it here.
- [ ] How invalid / 50-50 resolutions appear in Gamma (`outcomePrices`? `umaResolutionStatus`?).
