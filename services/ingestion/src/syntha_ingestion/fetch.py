"""Sampling and fetching jobs. Everything fetched lands in the raw layer; nothing is normalized
here. Jobs are resumable: completed units are checkpointed and skipped on the next run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

from syntha_ingestion import polymarket as pm
from syntha_ingestion.config import IngestSettings
from syntha_ingestion.http import ApiClient, FetchError
from syntha_ingestion.parse import json_list, open_ts, parse_ts
from syntha_ingestion.raw_store import RawStore
from syntha_ingestion.sampling import Candidate, stratified_sample

log = logging.getLogger(__name__)

# Price samples are requested until this long after trading closed.
POST_CLOSE_PADDING = timedelta(hours=1)


# ------------------------------------------------------------------ checkpoints


class Checkpoints:
    def __init__(self, directory: Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.done_path = directory / f"{name}.jsonl"
        self.failed_path = directory / f"{name}.failures.jsonl"
        self.done: set[str] = set()
        if self.done_path.exists():
            for line in self.done_path.read_text().splitlines():
                if line.strip():
                    self.done.add(json.loads(line)["unit"])

    def mark(self, unit: str, **info: Any) -> None:
        self.done.add(unit)
        with self.done_path.open("a") as f:
            f.write(json.dumps({"unit": unit, "at": _now_iso(), **info}, sort_keys=True) + "\n")

    def fail(self, unit: str, error: str) -> None:
        with self.failed_path.open("a") as f:
            f.write(json.dumps({"unit": unit, "at": _now_iso(), "error": error}) + "\n")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ sample


async def sample_collection(
    settings: IngestSettings,
    *,
    name: str,
    end_date_min: str,
    end_date_max: str,
    n: int,
    seed: int,
    client: ApiClient | None = None,
) -> dict[str, Any]:
    raw = RawStore(settings.raw_dir)
    if raw.manifest_path(name).exists():
        raise FileExistsError(
            f"collection manifest {name!r} already exists; manifests are immutable"
        )

    own_client = client is None
    api = client or ApiClient(raw, requests_per_second=settings.requests_per_second)
    candidates: list[Candidate] = []
    listed = {"closed": 0, "open": 0}
    try:
        for closed in (True, False):  # unresolved markets stay in the universe (PRD §5.1 rule 6)
            async for market in pm.list_markets(
                api, end_date_min=end_date_min, end_date_max=end_date_max, closed=closed
            ):
                listed["closed" if closed else "open"] += 1
                candidates.append(Candidate.from_gamma(market))
            log.info(
                "listed %d %s markets",
                listed["closed" if closed else "open"],
                "closed" if closed else "open",
            )
    finally:
        if own_client:
            await api.aclose()

    chosen = stratified_sample(candidates, n, seed)
    strata: dict[str, dict[str, int]] = {}
    for c in {c.condition_id: c for c in candidates}.values():
        strata.setdefault(c.stratum, {"available": 0, "selected": 0})["available"] += 1
    for c in chosen:
        strata[c.stratum]["selected"] += 1

    manifest = {
        "name": name,
        "created_at": _now_iso(),
        "definition": {
            "method": "stratified_random",
            "universe": "Gamma markets with end_date in [end_date_min, end_date_max], closed and open",
            "end_date_min": end_date_min,
            "end_date_max": end_date_max,
            "strata_by": "fee_category(feeType)",
            "allocation": "equal per stratum, capped by stratum size, remainder redistributed",
            "n_requested": n,
            "seed": seed,
            "excluded_fields": "no outcome-dependent field (volume, liquidity, resolution) was used",
        },
        "listed": listed,
        "strata": dict(sorted(strata.items())),
        "markets": [
            {"id": c.market_id, "condition_id": c.condition_id, "stratum": c.stratum}
            for c in chosen
        ],
    }
    raw.write_manifest(name, manifest)
    log.info(
        "collection %s: %d markets sampled from %d candidates",
        name,
        len(chosen),
        len({c.condition_id for c in candidates}),
    )
    return manifest


# ------------------------------------------------------------------ fetch


@dataclass
class FetchReport:
    units_done: int = 0
    units_skipped: int = 0
    units_failed: int = 0
    price_points: int = 0
    prints: int = 0
    notes: list[str] = field(default_factory=list)


async def _run_units(
    units: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]],
    checkpoints: Checkpoints,
    report: FetchReport,
    concurrency: int,
    phase: str,
) -> None:
    sem = asyncio.Semaphore(concurrency)
    total = len(units)
    done = 0

    async def run(key: str, job: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        nonlocal done
        if key in checkpoints.done:
            report.units_skipped += 1
            return
        async with sem:
            try:
                info = await job()
            except FetchError as exc:
                report.units_failed += 1
                checkpoints.fail(key, str(exc))
                log.error("%s failed: %s", key, exc)
                return
        checkpoints.mark(key, **info)
        report.units_done += 1
        done += 1
        if done % 50 == 0:
            log.info("%s: %d/%d units fetched", phase, done, total)

    await asyncio.gather(*(run(k, j) for k, j in units))
    log.info(
        "%s: done (%d units, %d skipped as already fetched)", phase, total, report.units_skipped
    )


def latest_markets(raw: RawStore, market_ids: set[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in raw.records(pm.GAMMA_MARKET):
        mid = str(rec.unit.get("id"))
        if rec.status == 200 and mid in market_ids:
            out[mid] = rec.json()  # later fetches overwrite earlier ones
    return out


async def fetch_collection(
    settings: IngestSettings,
    name: str,
    *,
    client: ApiClient | None = None,
    now: datetime | None = None,
) -> FetchReport:
    raw = RawStore(settings.raw_dir)
    manifest_path = raw.manifest_path(name)
    if not manifest_path.exists():
        raise FileNotFoundError(f"no manifest for collection {name!r}; run `sample` first")
    manifest = json.loads(manifest_path.read_text())
    market_ids = [m["id"] for m in manifest["markets"]]
    checkpoints = Checkpoints(settings.checkpoint_dir, name)
    report = FetchReport()
    now = now or datetime.now(UTC)

    own_client = client is None
    api = client or ApiClient(raw, requests_per_second=settings.requests_per_second)
    try:
        # 1. market metadata
        async def market_job(mid: str) -> dict[str, Any]:
            await pm.fetch_market(api, mid)
            return {}

        await _run_units(
            [(f"market:{m}", partial(market_job, m)) for m in market_ids],
            checkpoints,
            report,
            settings.concurrency,
            "markets",
        )
        markets = latest_markets(raw, set(market_ids))
        missing = set(market_ids) - set(markets)
        if missing:
            report.notes.append(f"{len(missing)} markets have no metadata")

        # 2. events (for tags)
        event_ids = sorted(
            {str(ev["id"]) for m in markets.values() for ev in (m.get("events") or [])}
        )

        async def event_job(eid: str) -> dict[str, Any]:
            await pm.fetch_event(api, eid)
            return {}

        await _run_units(
            [(f"event:{e}", partial(event_job, e)) for e in event_ids],
            checkpoints,
            report,
            settings.concurrency,
            "events",
        )

        # 3. resolutions (≤20 conditions per call)
        conditions = sorted({m["conditionId"] for m in markets.values()})

        async def resolution_job(batch: list[str]) -> dict[str, Any]:
            rows = await pm.fetch_resolutions(api, batch)
            return {"rows": len(rows)}

        await _run_units(
            [
                (f"resolutions:{b[0]}:{len(b)}", partial(resolution_job, b))
                for b in pm.resolution_batches(conditions)
            ],
            checkpoints,
            report,
            settings.concurrency,
            "resolutions",
        )

        # 4. trade prints, full history per market
        async def trades_job(cid: str) -> dict[str, Any]:
            count = await pm.walk_trades(api, cid)
            report.prints += count
            return {"prints": count}

        await _run_units(
            [(f"trades:{c}", partial(trades_job, c)) for c in conditions],
            checkpoints,
            report,
            settings.concurrency,
            "trades",
        )

        # 5. 1-minute price samples, both tokens, in CLOB-sized windows
        price_units: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = []
        for m in sorted(markets.values(), key=lambda m: m["conditionId"]):
            start = open_ts(m)
            if start is None:
                report.notes.append(f"{m['conditionId']}: no open timestamp; prices skipped")
                continue
            closed = parse_ts(m.get("closedTime"))
            end = min(now, closed + POST_CLOSE_PADDING) if closed else now
            start_s = int(start.timestamp()) // 60 * 60
            end_s = int(end.timestamp())
            for token in json_list(m.get("clobTokenIds")):
                for ws, we in pm.price_windows(start_s, end_s):

                    async def price_job(
                        t: str = str(token), a: int = ws, b: int = we
                    ) -> dict[str, Any]:
                        points = await pm.fetch_price_window(api, t, a, b)
                        report.price_points += points
                        return {"points": points}

                    price_units.append((f"prices:{token}:{ws}:{we}", price_job))
        await _run_units(price_units, checkpoints, report, settings.concurrency, "prices")
    finally:
        if own_client:
            await api.aclose()
        log.info(
            "requests=%d retries=%d failures=%d",
            api.stats.requests,
            api.stats.retries,
            api.stats.failures,
        )
    return report
