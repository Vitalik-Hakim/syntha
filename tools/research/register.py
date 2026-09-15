"""The hypothesis register (PRD v3 §3.6, E6): one row per registered configuration, cell,
wallet rule or scan variant, written by tooling before anything runs.

The register is `docs/research/REGISTER.md`, a Markdown table so it is readable in the repo and
diffable in review. A definition is identified by the hash of its canonical JSON; the sweep and
run tools refuse definitions without a row. `pooled.py` reads N per family from here.

Usage:
  uv run python tools/research/register.py add --study S --family F --data-seen "..." FILE...
  uv run python tools/research/register.py add-grid --study S --grid 1|2|both --data-seen "..."
  uv run python tools/research/register.py import-sweeps --data-seen "..."   # retro-register
  uv run python tools/research/register.py add-registration research/studies/<id>/registration.yaml
  uv run python tools/research/register.py set --id R-0007 --status done --result "..."
  uv run python tools/research/register.py list [--study S]
  uv run python tools/research/register.py check FILE...   # exit 1 if any is unregistered
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "docs" / "research" / "REGISTER.md"
RESEARCH_DIR = Path(os.environ.get("SYNTHA_DATA_DIR", "data")) / "research"
COLUMNS = (
    "id",
    "study",
    "family",
    "definition_hash",
    "registered_at",
    "data_seen_at_registration",
    "status",
    "result",
)
STATUSES = ("registered", "running", "done", "withdrawn")
HEADER = """# Hypothesis register (PRD v3 §3.6)

Every configuration, cell, wallet rule or scan variant is a row here **before** it runs; the
tools refuse unregistered definitions. `definition_hash` is the first 16 hex digits of the SHA-256
of the definition's canonical JSON. `data_seen_at_registration` records what the registrant had
already looked at. N for the best-of-N null (PRD §15.4, v3 §6.3) is the row count per family.
Rows are appended by `tools/research/register.py`; never edit results by hand.

"""


def definition_hash(definition: Any) -> str:
    text = json.dumps(
        definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def family_of_key(key: str) -> str:
    """`fav_hold_p0.9_h72` → `fav_hold`: the key up to its first parameter token."""
    parts = []
    for token in key.split("_"):
        if re.match(r"^[a-z]\d", token):
            break
        parts.append(token)
    return "_".join(parts) or key


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def load() -> list[dict[str, str]]:
    if not REGISTER.exists():
        return []
    rows = []
    for line in REGISTER.read_text().splitlines():
        if not line.startswith("| R-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) != len(COLUMNS):
            raise ValueError(f"malformed register row: {line!r}")
        rows.append(dict(zip(COLUMNS, cells, strict=True)))
    return rows


def save(rows: list[dict[str, str]]) -> None:
    lines = [
        HEADER.rstrip("\n"),
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    for r in rows:
        lines.append("| " + " | ".join(_cell(r[c]) for c in COLUMNS) + " |")
    REGISTER.parent.mkdir(parents=True, exist_ok=True)
    REGISTER.write_text("\n".join(lines) + "\n")


def by_hash() -> dict[str, dict[str, str]]:
    return {r["definition_hash"]: r for r in load()}


def registered(definition: Any) -> dict[str, str] | None:
    return by_hash().get(definition_hash(definition))


def add(
    study: str,
    family: str,
    definition: Any,
    data_seen: str,
    status: str = "registered",
    result: str = "",
) -> dict[str, str]:
    """Append a row unless the definition is already registered (then return its row)."""
    rows = load()
    digest = definition_hash(definition)
    for r in rows:
        if r["definition_hash"] == digest:
            return r
    row = {
        "id": f"R-{len(rows) + 1:04d}",
        "study": study,
        "family": family,
        "definition_hash": digest,
        "registered_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_seen_at_registration": data_seen,
        "status": status,
        "result": result,
    }
    rows.append(row)
    save(rows)
    return row


def set_status(row_id: str, status: str | None, result: str | None) -> dict[str, str]:
    rows = load()
    for r in rows:
        if r["id"] == row_id:
            if status is not None:
                if status not in STATUSES:
                    raise ValueError(f"status must be one of {STATUSES}")
                r["status"] = status
            if result is not None:
                r["result"] = result
            save(rows)
            return r
    raise KeyError(row_id)


def family_counts(exclude_withdrawn: bool = True) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in load():
        if exclude_withdrawn and r["status"] == "withdrawn":
            continue
        counts[r["family"]] = counts.get(r["family"], 0) + 1
    return counts


def require_registered(definitions: list[tuple[str, Any]]) -> None:
    """Refuse to run anything without a register row (PRD v3 §3.6)."""
    known = by_hash()
    missing = [
        (key, definition_hash(d)) for key, d in definitions if definition_hash(d) not in known
    ]
    if missing:
        listed = "\n".join(f"  {key}  ({digest})" for key, digest in missing)
        raise SystemExit(
            f"{len(missing)} definition(s) have no row in {REGISTER.relative_to(ROOT)}:\n{listed}\n"
            "Register them first: uv run python tools/research/register.py add|add-grid ..."
        )


# ------------------------------------------------------------------ registrations (v3 §7.6)


def primary_cells(registration: dict[str, Any]) -> list[dict[str, Any]]:
    """Every primary cell a registration tests, as a definition dict per cell. `primary_cells`
    in the YAML names the dimensions; their values come from `cell_dimensions`."""
    dims = registration.get("cell_dimensions") or {}
    names = [d.strip() for d in str(registration.get("primary_cells", "")).split("x") if d.strip()]
    values = [dims[n] for n in names]
    return [
        {"study": registration["study"], "cell": dict(zip(names, combo, strict=True))}
        for combo in itertools.product(*values)
    ]


def add_registration(path: Path) -> tuple[dict[str, str], int]:
    """Register a study: one row for the registration itself and one per primary cell, plus one
    per extra `registered_rules` entry. Idempotent by hash. Returns (study row, rows added)."""
    reg = yaml.safe_load(path.read_text())
    study = str(reg["study"])
    seen = reg.get("data_seen_before_registration", "")
    seen_text = "; ".join(str(s) for s in seen) if isinstance(seen, list) else str(seen)
    before = len(load())
    study_row = add(study, study, {"registration": reg}, seen_text, "registered", "")
    for cell in primary_cells(reg):
        add(study, f"{study}:cells", cell, seen_text, "registered", "")
    for rule in reg.get("registered_rules") or []:
        add(study, f"{study}:{rule.get('family', 'rules')}", rule, seen_text, "registered", "")
    return study_row, len(load()) - before


# ------------------------------------------------------------------ CLI


def _grid_configs(which: str) -> list[dict[str, Any]]:
    sys.path.insert(0, str(Path(__file__).parent))
    from candidates import grid, grid2

    return {"1": grid(), "2": grid2(), "both": grid() + grid2()}[which]


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="register strategy JSON files")
    p.add_argument("files", nargs="+")
    p.add_argument("--study", required=True)
    p.add_argument("--family", required=True)
    p.add_argument("--data-seen", required=True)
    p.add_argument("--status", default="registered", choices=STATUSES)
    p.add_argument("--result", default="")

    p = sub.add_parser("add-grid", help="register every configuration of a candidate grid")
    p.add_argument("--study", required=True)
    p.add_argument("--grid", default="1", choices=["1", "2", "both"])
    p.add_argument("--data-seen", required=True)
    p.add_argument("--status", default="registered", choices=STATUSES)
    p.add_argument("--result", default="")

    p = sub.add_parser("import-sweeps", help="retro-register everything a past sweep ran")
    p.add_argument("--data-seen", required=True)
    p.add_argument("--result", default="see docs/RESEARCH_PROTOCOL.md")

    p = sub.add_parser("add-registration", help="register a study from its registration.yaml")
    p.add_argument("path")

    p = sub.add_parser("set", help="update a row's status/result")
    p.add_argument("--id", required=True)
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--result")

    p = sub.add_parser("list")
    p.add_argument("--study")

    p = sub.add_parser("check", help="exit 1 unless every file is registered")
    p.add_argument("files", nargs="+")

    args = ap.parse_args()
    if args.cmd == "add":
        for path in args.files:
            dsl = json.loads(Path(path).read_text())
            row = add(args.study, args.family, dsl, args.data_seen, args.status, args.result)
            print(row["id"], row["definition_hash"], dsl.get("name", path))
    elif args.cmd == "add-grid":
        for cfg in _grid_configs(args.grid):
            row = add(
                args.study,
                family_of_key(cfg["key"]),
                cfg["dsl"],
                args.data_seen,
                args.status,
                args.result,
            )
            print(row["id"], row["definition_hash"], cfg["key"])
    elif args.cmd == "import-sweeps":
        for grid_path in sorted(RESEARCH_DIR.glob("*/grid.json")):
            meta = json.loads(grid_path.read_text())
            for cfg in meta["configs"]:
                row = add(
                    f"sweep:{grid_path.parent.name}",
                    family_of_key(cfg["key"]),
                    cfg["dsl"],
                    args.data_seen,
                    "done",
                    args.result,
                )
                print(row["id"], row["definition_hash"], grid_path.parent.name, cfg["key"])
    elif args.cmd == "add-registration":
        row, n = add_registration(Path(args.path))
        print(
            f"{row['id']} {row['study']}: {n} new row(s); {family_counts().get(row['study'] + ':cells', 0)} primary cells"
        )
    elif args.cmd == "set":
        row = set_status(args.id, args.status, args.result)
        print(json.dumps(row, indent=1))
    elif args.cmd == "list":
        for r in load():
            if args.study and r["study"] != args.study:
                continue
            print(
                f"{r['id']}  {r['study']:<24} {r['family']:<16} {r['definition_hash']}  {r['status']:<10} {r['result']}"
            )
    elif args.cmd == "check":
        require_registered([(p, json.loads(Path(p).read_text())) for p in args.files])
        print("all registered")


if __name__ == "__main__":
    main()
