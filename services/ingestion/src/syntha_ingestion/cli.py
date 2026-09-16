"""`syntha-ingest` — sample, fetch, normalize, verify and report on the dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, replace

import psycopg

from syntha_ingestion import census
from syntha_ingestion.config import IngestSettings, psycopg_url
from syntha_ingestion.fetch import fetch_collection, sample_collection
from syntha_ingestion.normalize import rebuild, verify


def _settings(args: argparse.Namespace) -> IngestSettings:
    s = IngestSettings()
    if getattr(args, "rps", None):
        s = replace(s, requests_per_second=args.rps)
    return s


def cmd_sample(args: argparse.Namespace) -> int:
    manifest = asyncio.run(
        sample_collection(
            _settings(args),
            name=args.name,
            end_date_min=args.end_date_min,
            end_date_max=args.end_date_max,
            n=args.n,
            seed=args.seed,
            categories=args.categories.split(",") if args.categories else None,
            min_lifetime_volume=args.min_lifetime_volume,
            from_raw_listing=args.from_raw_listing,
        )
    )
    print(
        json.dumps(
            {
                "name": manifest["name"],
                "markets": len(manifest["markets"]),
                "listed": manifest["listed"],
                "strata": manifest["strata"],
            },
            indent=2,
        )
    )
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    report = asyncio.run(fetch_collection(_settings(args), args.name))
    print(json.dumps(asdict(report), indent=2))
    if report.units_failed:
        print(
            f"{report.units_failed} units failed; see data/checkpoints/{args.name}.failures.jsonl. "
            "Re-run fetch to retry them.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    result = rebuild(
        _settings(args), truncate_first=args.truncate_first, min_free_gb=args.min_free_gb
    )
    print(
        json.dumps(
            {
                "dataset_version_id": str(result.dataset_version_id),
                "content_hash": result.content_hash,
                "counts": result.counts,
            },
            indent=2,
        )
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    current, recorded = verify(_settings(args))
    print(json.dumps({"current_hash": current, "latest_dataset_version_hash": recorded}, indent=2))
    if current != recorded:
        print("normalized tables do not match the latest dataset version", file=sys.stderr)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    s = _settings(args)
    with psycopg.connect(psycopg_url(s.database_url)) as conn:
        row = conn.execute(
            "SELECT id, built_at, content_hash, quality FROM dataset_versions "
            "ORDER BY built_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        print("no dataset version yet; run `normalize`", file=sys.stderr)
        return 1
    quality = row[3]
    if not args.full:
        quality = {k: v for k, v in quality.items() if k != "build"} | {
            "build": {
                k: v
                for k, v in quality["build"].items()
                if k in ("rows_in", "rows_written", "rows_rejected")
            }
            | {
                "markets_dropped": len(quality["build"]["markets_dropped"]),
                "resolution_anomalies": len(quality["build"]["resolution_anomalies"]),
            }
        }
    print(
        json.dumps(
            {
                "dataset_version_id": str(row[0]),
                "built_at": row[1].isoformat(),
                "content_hash": row[2],
                "quality": quality,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_census_sample(args: argparse.Namespace) -> int:
    manifest = asyncio.run(
        census.sample_census(
            _settings(args),
            month=args.month,
            seed=args.seed,
            per_stratum=args.per_stratum,
            ultra_short_cap=args.ultra_short_cap,
            concurrency=args.concurrency,
        )
    )
    print(
        json.dumps(
            {"name": manifest["name"], "markets": len(manifest["markets"]), **manifest["frame"]},
            indent=2,
        )
    )
    return 0


def cmd_census_fetch(args: argparse.Namespace) -> int:
    report = asyncio.run(
        census.fetch_census(
            _settings(args),
            args.month,
            concurrency=args.concurrency,
            prices_rps=args.prices_rps,
        )
    )
    print(json.dumps(asdict(report), indent=2))
    if report.units_failed:
        print(
            f"{report.units_failed} units failed; see data/checkpoints/census-{args.month}"
            ".failures.jsonl. Re-run census-fetch to retry them.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_census_build(args: argparse.Namespace) -> int:
    result = census.build_census(_settings(args), args.month)
    print(
        json.dumps(
            {
                "census_id": result.census_id,
                "content_hash": result.content_hash,
                "n_markets": result.n_markets,
                "n_observations": result.n_observations,
                "quality": {k: v for k, v in result.quality.items() if k != "markets_dropped"},
            },
            indent=2,
        )
    )
    return 0


def cmd_census_verify(args: argparse.Namespace) -> int:
    current, recorded = census.verify_census(_settings(args), args.month)
    print(json.dumps({"current_hash": current, "recorded_hash": recorded}, indent=2))
    if current != recorded:
        print("census tables do not match the recorded build", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="syntha-ingest", description=__doc__)
    p.add_argument(
        "--rps", type=float, help="requests per second (default 5, env SYNTHA_INGEST_RPS)"
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser(
        "sample", help="draw a seeded stratified sample of markets into a collection"
    )
    s.add_argument("--name", required=True)
    s.add_argument("--end-date-min", required=True, help="ISO-8601, e.g. 2026-07-01T00:00:00Z")
    s.add_argument("--end-date-max", required=True)
    s.add_argument("--n", type=int, default=600)
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--categories", help="comma-separated fee categories, e.g. sports,politics")
    s.add_argument(
        "--min-lifetime-volume",
        type=float,
        help="pre-filter on FINAL volume (recorded in the manifest; see its warning)",
    )
    s.add_argument(
        "--from-raw-listing",
        action="store_true",
        help="reuse the saved Gamma listing for this window instead of re-listing",
    )
    s.set_defaults(func=cmd_sample)

    f = sub.add_parser("fetch", help="fetch metadata, resolutions, prints and prices into raw/")
    f.add_argument("--name", required=True)
    f.set_defaults(func=cmd_fetch)

    n = sub.add_parser("normalize", help="rebuild normalized tables from raw/")
    n.add_argument(
        "--truncate-first",
        action="store_true",
        help="drop the current layer before loading so peak disk use is the new layer alone",
    )
    n.add_argument(
        "--min-free-gb",
        type=float,
        default=0.0,
        help="refuse to start with less than this much free space under the data dir",
    )
    n.set_defaults(func=cmd_normalize)
    sub.add_parser(
        "verify", help="check normalized tables match the latest dataset version"
    ).set_defaults(func=cmd_verify)
    r = sub.add_parser("report", help="print the latest data-quality report")
    r.add_argument("--full", action="store_true", help="include every dropped market and anomaly")
    r.set_defaults(func=cmd_report)

    # --- calibration census (PRD v3 §4.1)
    cs = sub.add_parser("census-sample", help="list a month, fetch its events, draw the census")
    cs.add_argument("--month", required=True, help="YYYY-MM, e.g. 2025-07")
    cs.add_argument("--seed", type=int, required=True)
    cs.add_argument("--per-stratum", type=int, default=census.PER_STRATUM)
    cs.add_argument("--ultra-short-cap", type=int, default=census.ULTRA_SHORT_CAP)
    cs.add_argument("--concurrency", type=int)
    cs.set_defaults(func=cmd_census_sample)
    cf = sub.add_parser(
        "census-fetch", help="fetch prices, prints and resolutions for a census (resumable)"
    )
    cf.add_argument("--month", required=True)
    cf.add_argument("--concurrency", type=int)
    cf.add_argument(
        "--prices-rps", type=float, help="separate rate for the CLOB price phase (limit 100/s)"
    )
    cf.set_defaults(func=cmd_census_fetch)
    cb = sub.add_parser("census-build", help="build census_* tables from raw/ and hash them")
    cb.add_argument("--month", required=True)
    cb.set_defaults(func=cmd_census_build)
    cv = sub.add_parser("census-verify", help="recompute the census hash and compare")
    cv.add_argument("--month", required=True)
    cv.set_defaults(func=cmd_census_verify)

    args = p.parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
