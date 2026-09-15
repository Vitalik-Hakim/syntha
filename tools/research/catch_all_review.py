"""Catch-all review (PRD v3 §4.2; M9 decision: a catch-all requires review).

A neg-risk event is exhaustive only if it lists a residual market ("Other", "None of the
above", "No winner"). Nothing infers that automatically any more: the loader hands the engine
`is_catch_all` only from `catch_all_reviews`, which this tool writes. Candidates come from
Gamma's `negRiskOther` flag and the text rule in `syntha_worker.loader.CATCH_ALL`.

Usage:
  uv run python tools/research/catch_all_review.py list [--collection NAME] [--all]
  uv run python tools/research/catch_all_review.py set <condition_id> yes|no [--note "..."]
  uv run python tools/research/catch_all_review.py export   # → docs/research/catch_all_reviews.json
  uv run python tools/research/catch_all_review.py import   # ← the same file (after db-reset)
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from syntha_worker.loader import CATCH_ALL

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "docs" / "research" / "catch_all_reviews.json"


def _url() -> str:
    url = os.environ.get("SYNTHA_DATABASE_URL", "postgresql://syntha:syntha@localhost:5432/syntha")
    return url.replace("postgresql+psycopg://", "postgresql://")


def candidates(
    conn: psycopg.Connection[Any], collection: str | None, everything: bool
) -> list[tuple[Any, ...]]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.event_id, m.question, m.neg_risk_other, r.is_catch_all, r.note,
               (SELECT count(*) FROM markets s WHERE s.event_id = m.event_id) AS siblings
        FROM markets m LEFT JOIN catch_all_reviews r ON r.condition_id = m.condition_id
        WHERE m.neg_risk
          AND (%(collection)s::text IS NULL OR m.condition_id IN (
                SELECT cm.condition_id FROM collection_markets cm
                JOIN collections c ON c.id = cm.collection_id WHERE c.name = %(collection)s))
        ORDER BY m.event_id, m.condition_id
        """,
        {"collection": collection},
    ).fetchall()
    out = []
    for cid, eid, question, other, reviewed, note, siblings in rows:
        flagged = bool(other) or bool(question and CATCH_ALL.search(question))
        if flagged or everything:
            out.append((cid, eid, question, bool(other), reviewed, note, siblings))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "list", help="candidates (negRiskOther or text rule) with their review state"
    )
    p.add_argument("--collection")
    p.add_argument("--all", action="store_true", help="every neg-risk market, not only candidates")
    p = sub.add_parser("set", help="record a verdict")
    p.add_argument("condition_id")
    p.add_argument("verdict", choices=["yes", "no"])
    p.add_argument("--note", default="")
    sub.add_parser("export", help=f"write every review to {EXPORT.relative_to(ROOT)}")
    sub.add_parser("import", help="load the exported reviews into the database (upsert)")
    args = ap.parse_args()

    with psycopg.connect(_url()) as conn:
        if args.cmd == "list":
            for cid, eid, question, other, reviewed, note, siblings in candidates(
                conn, args.collection, args.all
            ):
                state = (
                    "unreviewed"
                    if reviewed is None
                    else ("CATCH-ALL" if reviewed else "not catch-all")
                )
                flag = " negRiskOther" if other else ""
                print(
                    f"{cid}  event={eid} siblings={siblings}{flag}  [{state}]  {question!r}"
                    + (f"  — {note}" if note else "")
                )
        elif args.cmd == "set":
            conn.execute(
                "INSERT INTO catch_all_reviews (condition_id, is_catch_all, reviewed_at, note) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (condition_id) DO UPDATE SET "
                "is_catch_all = excluded.is_catch_all, reviewed_at = excluded.reviewed_at, note = excluded.note",
                (args.condition_id, args.verdict == "yes", datetime.now(UTC), args.note or None),
            )
            conn.commit()
            print(
                f"{args.condition_id}: {'catch-all' if args.verdict == 'yes' else 'not a catch-all'}"
            )
        elif args.cmd == "export":
            rows = conn.execute(
                "SELECT condition_id, is_catch_all, reviewed_at, note FROM catch_all_reviews ORDER BY 1"
            ).fetchall()
            EXPORT.parent.mkdir(parents=True, exist_ok=True)
            EXPORT.write_text(
                json.dumps(
                    [
                        {
                            "condition_id": c,
                            "is_catch_all": v,
                            "reviewed_at": t.isoformat(),
                            "note": n,
                        }
                        for c, v, t, n in rows
                    ],
                    indent=1,
                )
                + "\n"
            )
            print(f"{len(rows)} reviews → {EXPORT}")
        elif args.cmd == "import":
            rows = json.loads(EXPORT.read_text()) if EXPORT.exists() else []
            for r in rows:
                conn.execute(
                    "INSERT INTO catch_all_reviews (condition_id, is_catch_all, reviewed_at, note) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (condition_id) DO UPDATE SET "
                    "is_catch_all = excluded.is_catch_all, reviewed_at = excluded.reviewed_at, note = excluded.note",
                    (r["condition_id"], r["is_catch_all"], r["reviewed_at"], r.get("note")),
                )
            conn.commit()
            print(f"{len(rows)} reviews loaded")


if __name__ == "__main__":
    main()
