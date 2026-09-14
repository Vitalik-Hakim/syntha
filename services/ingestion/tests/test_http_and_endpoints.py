import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from syntha_ingestion import polymarket as pm
from syntha_ingestion.config import PRICE_WINDOW_SECONDS
from syntha_ingestion.http import ApiClient, FetchError
from syntha_ingestion.raw_store import RawStore


def _client(tmp_path: Path, handler: object, sleeps: list[float] | None = None) -> ApiClient:
    async def fake_sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    return ApiClient(
        RawStore(tmp_path / "raw"),
        requests_per_second=1000,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        sleep=fake_sleep,
    )


def test_retry_after_is_honoured_and_only_final_response_recorded(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="busy")
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []

    async def run() -> None:
        async with _client(tmp_path, handler, sleeps) as api:
            record, body = await api.get("ep", "https://x.test/a", {"q": 1}, {"kind": "t"})
            assert body == {"ok": True}
            assert record.json() == {"ok": True}

    asyncio.run(run())
    assert calls["n"] == 2
    assert sleeps and sleeps[0] >= 7
    records = RawStore(tmp_path / "raw").records("ep")
    assert [r.status for r in records] == [200]


def test_non_retryable_error_raises_and_is_recorded(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad"})

    async def run() -> None:
        async with _client(tmp_path, handler) as api:
            with pytest.raises(FetchError):
                await api.get("ep", "https://x.test/a", {}, {})

    asyncio.run(run())
    assert [r.status for r in RawStore(tmp_path / "raw").records("ep")] == [400]


def test_walk_trades_resends_condition_on_every_page(tmp_path: Path) -> None:
    """Regression guard for the silent-truncation trap in docs/DATA_NOTES.md."""
    seen: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        seen.append(q)
        if "condition" not in q:  # what the real API does with a bare cursor
            return httpx.Response(
                200, json={"data": [], "pagination": {"has_more": False, "next_cursor": None}}
            )
        page = len(seen)
        nxt = f"c{page}" if page < 3 else None
        return httpx.Response(
            200,
            json={
                "data": [{"i": page}] * 2,
                "pagination": {"has_more": nxt is not None, "next_cursor": nxt},
            },
        )

    async def run() -> int:
        async with _client(tmp_path, handler) as api:
            return await pm.walk_trades(api, "0xcond")

    assert asyncio.run(run()) == 6
    assert len(seen) == 3
    assert all(q["condition"] == ["0xcond"] for q in seen)
    assert "cursor" not in seen[0] and seen[1]["cursor"] == ["c1"]


def test_price_windows_cover_range_without_exceeding_clob_limit() -> None:
    start, end = 1_700_000_000, 1_700_000_000 + 40 * 86_400 + 17
    windows = pm.price_windows(start, end)
    assert windows[0][0] == start and windows[-1][1] == end
    assert all(b - a <= PRICE_WINDOW_SECONDS for a, b in windows)
    assert all(windows[i][1] == windows[i + 1][0] for i in range(len(windows) - 1))
    assert pm.price_windows(5, 5) == []


def test_resolution_batches_respect_api_cap() -> None:
    batches = pm.resolution_batches([f"0x{i}" for i in range(45)] + ["0x1"])
    assert [len(b) for b in batches] == [20, 20, 5]
