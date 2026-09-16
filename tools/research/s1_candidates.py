"""Turn S1 discovery candidates into DSL strategies for confirmation (PRD v3 §5.1).

Each candidate cell (category × fill-price bucket × offset) becomes one strategy: universe =
the category, either side; entry = price inside the bucket while the time to the scheduled end
is inside the offset's window; hold to settlement; LIMIT +1 tick, TTL 5; 2% of starting capital;
one position per event. The documents are written next to the report and registered as the
executable form of their (already registered) cells. Run them with

  uv run python tools/research/run_candidates.py --collection census-2026-06 --bar-interval 60 \
      --all-presets research/studies/s1_calibration/candidates/*.json

Usage:
  uv run python tools/research/s1_calibration.py discovery ...        # writes summary.json
  uv run python tools/research/s1_candidates.py [--summary PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from register import add

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "research" / "studies" / "s1_calibration"

# Entry windows in minutes to the scheduled end, one per census offset; hourly bars cannot
# resolve 15 minutes, so the last two offsets share the final hour and the one before it.
OFFSET_WINDOWS: dict[str, tuple[int, int]] = {
    "-7d": (7 * 1440, 8 * 1440),
    "-3d": (3 * 1440, 4 * 1440),
    "-24h": (24 * 60, 30 * 60),
    "-6h": (6 * 60, 9 * 60),
    "-1h": (60, 180),
    "-15m": (0, 60),
}


def bucket_bounds(label: str) -> tuple[float, float]:
    lo, hi = label.strip("[]()").split(",")
    return float(lo), float(hi)


def candidate_dsl(c: dict[str, Any]) -> dict[str, Any]:
    lo, hi = bucket_bounds(c["bucket"])
    ttr_lo, ttr_hi = OFFSET_WINDOWS[c["offset"]]
    return {
        "name": f"S1 candidate: {c['category']} {c['bucket']} at {c['offset']}",
        "description": (
            f"PRD v3 S1 confirmation strategy for the discovery candidate cell category={c['category']}, "
            f"fill-price bucket {c['bucket']}, offset {c['offset']} (discovery E[r] {c['e_r']:+.3f}, "
            f"n {c['n_fill']}, fill rate {c['fill_rate']:.0%}). Either side of each market; enter when the "
            f"token's price is inside the bucket while {ttr_lo}–{ttr_hi} minutes remain to the scheduled end; "
            "hold to settlement; LIMIT +1 tick, TTL 5 bars; 2% of starting capital; one position per event. "
            "Runs on census collections at 60-minute bars."
        ),
        "universe": {"side": "EITHER", "categories": [c["category"]]},
        "entry": {
            "all": [
                {"feature": "price", "params": {}, "op": "gte", "value": lo},
                {"feature": "price", "params": {}, "op": "lt", "value": hi},
                {
                    "feature": "time_to_resolution_minutes",
                    "params": {},
                    "op": "gte",
                    "value": ttr_lo,
                },
                {
                    "feature": "time_to_resolution_minutes",
                    "params": {},
                    "op": "lte",
                    "value": ttr_hi,
                },
            ]
        },
        "sizing": {
            "mode": "PCT_OF_INITIAL",
            "value": 0.02,
            "min_order_policy": "ROUND_UP",
            "round_up_max_multiple": 2.0,
        },
        "exit": {"any": []},
        "constraints": {
            "max_open_positions": 60,
            "max_total_exposure_pct": 0.9,
            "max_positions_per_event": 1,
            "cooldown_minutes_per_market": 0,
            "allow_reentry_same_market": False,
        },
        "order": {"type": "LIMIT", "limit_offset_ticks": 1, "ttl_bars": 5},
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--summary", default=str(STUDY / "report" / "summary.json"))
    ap.add_argument("--out", default=str(STUDY / "candidates"))
    args = ap.parse_args()
    summary = json.loads(Path(args.summary).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not summary["candidates"]:
        print("no candidates in the summary; nothing to confirm")
        return
    for i, c in enumerate(summary["candidates"], start=1):
        dsl = candidate_dsl(c)
        path = (
            out
            / f"c{i:02d}_{c['category']}_{c['bucket'].strip('[]()').replace(',', '-')}_{c['offset'].lstrip('-')}.json"
        )
        path.write_text(json.dumps(dsl, indent=2) + "\n")
        row = add(
            "s1_calibration",
            "s1_calibration:candidate-dsl",
            dsl,
            f"S1 discovery on {', '.join(summary['months'])}: this cell was a discovery candidate; "
            "the DSL is its executable form for C1/C2, not a new hypothesis",
            "registered",
            "",
        )
        print(row["id"], path.name)


if __name__ == "__main__":
    main()
