"""Rate-limited, retrying HTTP client that records every final response in the raw layer.

PRD §4.6: token-bucket rate limiter (default 5 req/s), exponential backoff with jitter, and
honouring the server's retry delay. Polymarket sends that as a `Retry-After` header, not the
`retry_after_seconds` body field the PRD assumed (docs/DATA_NOTES.md).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from syntha_ingestion.raw_store import RawRecord, RawStore

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({425, 429, 500, 502, 503, 504})


class TokenBucket:
    """Async token bucket: `rate` tokens per second, holding at most `capacity`."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(1.0, rate)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str) -> None:
        super().__init__(f"{url}: {status or 'no response'}: {detail[:300]}")
        self.url = url
        self.status = status


@dataclass
class FetchStats:
    requests: int = 0
    retries: int = 0
    failures: int = 0


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


class ApiClient:
    def __init__(
        self,
        raw: RawStore,
        *,
        requests_per_second: float = 5.0,
        max_retries: int = 6,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.raw = raw
        self.bucket = TokenBucket(requests_per_second)
        self.max_retries = max_retries
        self.stats = FetchStats()
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            timeout=timeout_s,
            transport=transport,
            headers={"User-Agent": "syntha-ingestion/0.0 (research backtester)"},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> ApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get(
        self, endpoint: str, url: str, params: dict[str, Any], unit: dict[str, Any]
    ) -> tuple[RawRecord, Any]:
        """GET `url`, store the final response under `endpoint`, return (record, parsed JSON).

        Raises FetchError for non-2xx after retries; the failed response is still recorded.
        """
        clean = {k: v for k, v in params.items() if v is not None}
        attempt = 0
        while True:
            await self.bucket.acquire()
            self.stats.requests += 1
            delay: float | None
            try:
                resp = await self._http.get(url, params=clean)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt >= self.max_retries:
                    self.stats.failures += 1
                    raise FetchError(url, None, repr(exc)) from exc
                delay = None
                detail = repr(exc)
                status = None
            else:
                status = resp.status_code
                if status < 400 or status not in RETRYABLE_STATUS or attempt >= self.max_retries:
                    record = self.raw.write(
                        endpoint=endpoint,
                        url=url,
                        params=clean,
                        unit=unit,
                        status=status,
                        body=resp.text,
                    )
                    if status >= 400:
                        self.stats.failures += 1
                        raise FetchError(url, status, resp.text)
                    return record, resp.json()
                delay = retry_after_seconds(resp.headers)
                detail = resp.text

            backoff = min(60.0, 2**attempt) + random.uniform(0, 1)
            wait = max(delay, backoff) if delay is not None else backoff
            attempt += 1
            self.stats.retries += 1
            log.warning(
                "retry %d for %s (%s) in %.1fs: %s", attempt, url, status, wait, detail[:120]
            )
            await self._sleep(wait)
