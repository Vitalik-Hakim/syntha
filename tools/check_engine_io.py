#!/usr/bin/env python3
"""Enforce the engine's no-I/O boundary (PRD §6).

`packages/engine` takes an in-memory Dataset and returns a result. It must not reach the
network, a database, the filesystem, a subprocess, or a sibling service package. This is a
static AST check so it catches the import even on code paths tests never execute.

Usage: python tools/check_engine_io.py <path> [<path> ...]
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        # network
        "aiohttp",
        "ftplib",
        "http",
        "httpx",
        "requests",
        "smtplib",
        "socket",
        "ssl",
        "urllib",
        "urllib3",
        "websocket",
        "websockets",
        # databases / queues
        "asyncpg",
        "psycopg",
        "psycopg2",
        "redis",
        "rq",
        "sqlalchemy",
        "sqlite3",
        # filesystem / process
        "glob",
        "io",
        "os",
        "pathlib",
        "pickle",
        "shelve",
        "shutil",
        "subprocess",
        "tempfile",
        # dynamic import would bypass this check
        "importlib",
        # sibling packages that own I/O
        "fastapi",
        "syntha_api",
        "syntha_ingestion",
        "syntha_recorder",
        "syntha_storage",
        "syntha_worker",
    }
)

FORBIDDEN_BUILTIN_CALLS: frozenset[str] = frozenset({"open", "input", "__import__", "exec", "eval"})

# Dataframe/array readers and writers, e.g. pl.read_csv, df.write_parquet, np.load.
FORBIDDEN_ATTR_PREFIXES: tuple[str, ...] = ("read_", "scan_", "write_", "sink_")
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {"load", "loadtxt", "save", "savez", "savetxt", "tofile", "fromfile"}
)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def _check_tree(path: Path, tree: ast.AST) -> Iterator[Violation]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root(alias.name) in FORBIDDEN_MODULES:
                    yield Violation(path, node.lineno, f"forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and _root(node.module) in FORBIDDEN_MODULES:
                yield Violation(path, node.lineno, f"forbidden import from '{node.module}'")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTIN_CALLS:
                yield Violation(path, node.lineno, f"forbidden call '{func.id}()'")
            elif isinstance(func, ast.Attribute) and (
                func.attr.startswith(FORBIDDEN_ATTR_PREFIXES) or func.attr in FORBIDDEN_ATTRS
            ):
                yield Violation(path, node.lineno, f"forbidden I/O call '.{func.attr}()'")


def _python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            yield from sorted(p.rglob("*.py"))
        elif p.suffix == ".py":
            yield p


def check(paths: Iterable[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for file in _python_files(paths):
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        violations.extend(_check_tree(file, tree))
    return violations


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    paths = [Path(a) for a in argv]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"path does not exist: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    violations = check(paths)
    for v in violations:
        print(v, file=sys.stderr)
    if violations:
        print(f"\n{len(violations)} engine I/O boundary violation(s). See PRD §6.", file=sys.stderr)
        return 1
    print(f"engine I/O boundary OK ({sum(1 for _ in _python_files(paths))} files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
