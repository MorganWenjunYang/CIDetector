"""Shared async HTTP client with rate-limiting and retry."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF = 1.0


class RateLimiter:
    """Token-bucket rate limiter (per-second)."""

    def __init__(self, max_per_second: float = 3.0):
        self._interval = 1.0 / max_per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


_limiters: dict[str, RateLimiter] = {}


def get_limiter(key: str, max_per_second: float = 3.0) -> RateLimiter:
    if key not in _limiters:
        _limiters[key] = RateLimiter(max_per_second)
    return _limiters[key]


async def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    rate_key: str | None = None,
    rate_limit: float = 3.0,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> Any:
    """GET *url* and return parsed JSON, with rate-limiting and retries."""
    if rate_key:
        limiter = get_limiter(rate_key, rate_limit)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, max_retries + 1):
            if rate_key:
                await limiter.acquire()
            try:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if attempt == max_retries:
                    raise
                await asyncio.sleep(_DEFAULT_BACKOFF * attempt)


async def fetch_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    rate_key: str | None = None,
    rate_limit: float = 3.0,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> str:
    """GET *url* and return response body as text."""
    if rate_key:
        limiter = get_limiter(rate_key, rate_limit)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, max_retries + 1):
            if rate_key:
                await limiter.acquire()
            try:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.text
            except (httpx.HTTPStatusError, httpx.TransportError):
                if attempt == max_retries:
                    raise
                await asyncio.sleep(_DEFAULT_BACKOFF * attempt)
    return ""  # unreachable, keeps type-checker happy
