# Decisions

## Open product decisions (PRD §20)

Defaults are implemented as stated. Each needs a product-owner answer.

| # | Question | Default in use | Status |
| --- | --- | --- | --- |
| 1 | Venue: international or US exchange? | International | Awaiting owner |
| 2 | Warn at $100 bankroll and suggest $1,000? | Yes, non-blocking hint | Awaiting owner |
| 3 | Multi-outcome events: one position per event? | One, configurable | Awaiting owner |
| 4 | Backtest history retention | Keep all | Awaiting owner |
| 5 | Self-hosted vs deployed | Single-user self-hosted | Awaiting owner |
| 6 | Legal review before any public distribution | n/a (not distributed) | Awaiting owner |

## Engineering decisions

### M0

- **Python workspace via uv.** One lockfile (`uv.lock`), one `.venv`, seven packages under
  `apps/`, `packages/`, `services/`, each with a `src/syntha_*` layout. Python pinned to 3.12.
- **Import names are prefixed `syntha_*`** (`syntha_engine`, `syntha_storage`, ...) so nothing
  collides with third-party packages named `engine`, `storage` or `contracts`.
- **The engine no-I/O boundary is a static AST check** (`tools/check_engine_io.py`), run as its
  own CI step and as a pytest test. It forbids network, database, filesystem and subprocess
  modules, `open()`/`__import__`/`eval`/`exec`, dataframe `read_*`/`write_*`/`scan_*`/`sink_*`
  calls, and imports of the I/O-owning sibling packages. If a legitimate need trips it, change
  the rule deliberately in that file, don't work around it.
- **The migration defines the DDL and is written by hand.** The ORM models must match it
  exactly; CI runs the migration against the Timescale image and asserts
  `alembic.autogenerate.compare_metadata` finds no differences.
- **Engine-restricted columns are tagged in the ORM** (`info={"engine_restricted": True}`) so the
  M1/M2 repository layer can strip them mechanically rather than by a hand-maintained list.
- **Health has two endpoints:** `/api/health` (liveness, no dependencies) and
  `/api/health/ready` (checks Postgres and Redis, returns 503 with a body when degraded).
- **The browser only calls its own origin.** Next.js rewrites `/api/*` to FastAPI, so there is
  no CORS configuration and no API URL in client bundles.
- **Contract TS types are hand-mirrored for now** (one type in `system-status.tsx`).
  Codegen from the Pydantic models lands in M3, when the API starts returning real data.
- **No web fonts.** The UI uses system sans and mono stacks, so builds work offline and no third
  party sees requests.
