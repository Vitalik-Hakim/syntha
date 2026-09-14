# Syntha

Backtesting research terminal for Polymarket. The spec is `docs/PRD.md`. Read §1–5 before
changing engine, storage or ingestion code.

## Constraints that must never be violated

- `packages/engine` performs no I/O. `make check-engine-io` enforces it; never weaken
  `tools/check_engine_io.py` just to get CI green.
- The strategy evaluator cannot see future bars or resolution data (`resolved_at`,
  `resolution`, `final_volume`, or anything derived from them). ORM columns tagged
  `engine_restricted` travel only to settlement.
- No metric is computed in the browser. Every number shown comes from `bt_trades`,
  `bt_equity` or `backtest_runs`.
- Missing data produces `UNEXECUTABLE`, never an interpolated or invented value.
- Milestones (PRD §18) are sequential. Don't start the next one until the current one's
  acceptance criteria pass in CI.
- When the live Polymarket API disagrees with the PRD, the API wins. Record the difference in
  `docs/DATA_NOTES.md`.
- `docs/PRD.md` stays verbatim. Deviations go in `docs/DATA_NOTES.md` / `docs/DECISIONS.md`.

## Working in the repo

- Python: `uv run ...` (workspace, Python 3.12). Web: `pnpm --dir apps/web ...`.
- `make lint`, `make test`, `make check-engine-io`, `make ci`.
- Schema changes need both a new Alembic revision in
  `packages/storage/src/syntha_storage/migrations/versions/` and a matching edit to
  `syntha_storage/models.py`. The migration test fails if they drift.
- Money and prices are `Decimal` / `numeric`, never float.
