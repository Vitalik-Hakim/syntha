"""The append-only raw layer (PRD §4.5).

Every API response is stored exactly as received, gzipped, under
`raw/<endpoint>/<fetch-date>/`, next to an append-only `index.jsonl` describing it. Nothing in
this directory is ever edited or deleted; the normalized layer is rebuilt from it alone.
Collection manifests live in `raw/_manifests/` because they are rebuild inputs too.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_DIR = "_manifests"


@dataclass(frozen=True)
class RawRecord:
    """One line of an endpoint's index.jsonl."""

    path: Path
    endpoint: str
    url: str
    params: dict[str, Any]
    unit: dict[str, Any]
    fetched_at: str  # ISO-8601 UTC, microsecond precision
    status: int
    sha256: str

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.fetched_at, self.path.name)

    def body_text(self) -> str:
        with gzip.open(self.path, "rt", encoding="utf-8") as f:
            envelope: dict[str, Any] = json.load(f)
        text: str = envelope["body"]
        return text

    def json(self) -> Any:
        return json.loads(self.body_text())


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write(
        self,
        *,
        endpoint: str,
        url: str,
        params: dict[str, Any],
        unit: dict[str, Any],
        status: int,
        body: str,
        fetched_at: datetime | None = None,
    ) -> RawRecord:
        fetched = (fetched_at or datetime.now(UTC)).astimezone(UTC)
        fetched_iso = fetched.isoformat(timespec="microseconds").replace("+00:00", "Z")
        day_dir = self.root / endpoint / fetched.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        envelope = {
            "endpoint": endpoint,
            "url": url,
            "params": params,
            "unit": unit,
            "fetched_at": fetched_iso,
            "status": status,
            "body": body,
        }
        payload = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        name = f"{fetched.strftime('%Y%m%dT%H%M%S%fZ')}-{digest[:16]}.json.gz"
        path = day_dir / name
        # mtime=0 so identical envelopes compress to identical bytes.
        with path.open("xb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
            gz.write(payload)

        record = RawRecord(path, endpoint, url, params, unit, fetched_iso, status, digest)
        line = {
            "file": name,
            "endpoint": endpoint,
            "url": url,
            "params": params,
            "unit": unit,
            "fetched_at": fetched_iso,
            "status": status,
            "sha256": digest,
        }
        with (day_dir / "index.jsonl").open("a", encoding="utf-8") as idx:
            idx.write(json.dumps(line, sort_keys=True) + "\n")
        return record

    def records(self, endpoint: str) -> list[RawRecord]:
        """All records for an endpoint, oldest fetch first (deterministic)."""
        out: list[RawRecord] = []
        base = self.root / endpoint
        if not base.exists():
            return out
        for index in sorted(base.glob("*/index.jsonl")):
            for line in index.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                out.append(
                    RawRecord(
                        path=index.parent / row["file"],
                        endpoint=row["endpoint"],
                        url=row["url"],
                        params=row["params"],
                        unit=row["unit"],
                        fetched_at=row["fetched_at"],
                        status=row["status"],
                        sha256=row["sha256"],
                    )
                )
        out.sort(key=lambda r: r.sort_key)
        return out

    # --------------------------------------------------------------- manifests

    def manifest_path(self, name: str) -> Path:
        return self.root / MANIFEST_DIR / f"{name}.json"

    def write_manifest(self, name: str, manifest: dict[str, Any]) -> Path:
        path = self.manifest_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as f:  # never overwrite: manifests are immutable
            json.dump(manifest, f, indent=2, sort_keys=True)
            f.write("\n")
        return path

    def manifests(self) -> Iterator[dict[str, Any]]:
        base = self.root / MANIFEST_DIR
        if not base.exists():
            return
        for path in sorted(base.glob("*.json")):
            manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            yield manifest
