"""Structural diff of JSON documents (strategy versions, run configs). Presentation of stored
configuration, not a metric."""

from __future__ import annotations

from typing import Any

_MISSING = object()


def flatten(doc: Any, prefix: str = "") -> dict[str, Any]:
    """Leaf values by dotted path; lists index as [i]. Empty containers are leaves."""
    if isinstance(doc, dict) and doc:
        out: dict[str, Any] = {}
        for key in sorted(doc):
            out.update(flatten(doc[key], f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(doc, list) and doc:
        out = {}
        for i, item in enumerate(doc):
            out.update(flatten(item, f"{prefix}[{i}]"))
        return out
    return {prefix: doc}


def diff(docs: list[Any], ignore: tuple[str, ...] = ()) -> list[tuple[str, list[Any]]]:
    """Paths whose values differ across the documents, in path order, with one value per
    document (None where a document lacks the path)."""
    flats = [flatten(d) for d in docs]
    paths = sorted(set().union(*flats)) if flats else []
    changes = []
    for path in paths:
        if any(path == p or path.startswith(f"{p}.") or path.startswith(f"{p}[") for p in ignore):
            continue
        values = [f.get(path, _MISSING) for f in flats]
        if len({repr(v) for v in values}) > 1:
            changes.append((path, [None if v is _MISSING else v for v in values]))
    return changes


def merge(base: Any, overrides: Any) -> Any:
    """Overrides applied to a JSON document: objects merge key by key, anything else replaces."""
    if isinstance(base, dict) and isinstance(overrides, dict):
        out = dict(base)
        for key, value in overrides.items():
            out[key] = merge(base.get(key), value) if key in base else value
        return out
    return overrides
