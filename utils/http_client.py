"""Shared async HTTP client with rate-limiting and retry."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF = 1.0
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def _merge_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Ensure every request carries a User-Agent header."""
    merged = {"User-Agent": _DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}
    if headers:
        merged.update(headers)
    return merged


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

    merged = _merge_headers(headers)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, max_retries + 1):
            if rate_key:
                await limiter.acquire()
            try:
                resp = await client.get(url, params=params, headers=merged)
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

    merged = _merge_headers(headers)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, max_retries + 1):
            if rate_key:
                await limiter.acquire()
            try:
                resp = await client.get(url, params=params, headers=merged)
                resp.raise_for_status()
                return resp.text
            except (httpx.HTTPStatusError, httpx.TransportError):
                if attempt == max_retries:
                    raise
                await asyncio.sleep(_DEFAULT_BACKOFF * attempt)
    return ""  # unreachable, keeps type-checker happy


async def fetch_text_browser(
    url: str,
    *,
    wait_until: str = "networkidle",
    timeout: float = 30000,
) -> str:
    """Fetch *url* via Playwright headless browser — bypasses JS anti-bot challenges."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=_DEFAULT_USER_AGENT,
            locale="zh-CN",
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until=wait_until, timeout=int(timeout))
        content = await page.content()
        await browser.close()
        return content


async def fetch_text_auto(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    rate_key: str | None = None,
    rate_limit: float = 3.0,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> str:
    """Try simple HTTP first; fall back to Playwright if blocked by anti-bot."""
    try:
        text = await fetch_text(
            url, params=params, headers=headers,
            rate_key=rate_key, rate_limit=rate_limit,
            timeout=timeout, max_retries=1,
        )
        if _looks_like_antibot(text):
            raise RuntimeError("anti-bot challenge detected")
        return text
    except Exception:
        full_url = url
        if params:
            from urllib.parse import urlencode
            full_url = f"{url}?{urlencode(params)}"
        return await fetch_text_browser(full_url, timeout=timeout * 1000)


def _looks_like_antibot(html: str) -> bool:
    """Heuristic: detect common anti-bot challenge pages."""
    markers = [
        "acw_sc__v2",
        "var arg1=",
        "document.location.reload()",
        "_cf_chl_opt",
        "challenge-platform",
    ]
    head = html[:3000].lower()
    return any(m.lower() in head for m in markers)
