#!/usr/bin/env python3
"""Web search via Tavily API.  Covers media sources, paywalled sites, and general queries.

Usage:
    python tools/web_search.py --query "B7H4 ADC pipeline 2026"
    python tools/web_search.py --query "B7H4" --site fiercebiotech.com
    python tools/web_search.py --query "百利天恒 BD deal" --days 90
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from utils.cache import cache_key, get as cache_get, put as cache_put

SOURCE = "WebSearch"
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


async def _tavily_search(
    query: str,
    *,
    max_results: int = 10,
    search_depth: str = "advanced",
    include_domains: list[str] | None = None,
    days: int | None = None,
) -> dict:
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=TAVILY_API_KEY)

    kwargs: dict = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": True,
    }
    if include_domains:
        kwargs["include_domains"] = include_domains
    if days:
        kwargs["days"] = days

    return await client.search(**kwargs)


def _parse_results(raw: dict, query: str) -> dict:
    items: list[dict] = []
    for r in raw.get("results", []):
        items.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "published_at": r.get("published_date", ""),
                "metadata": {
                    "score": r.get("score", 0),
                },
            }
        )

    return {
        "source": SOURCE,
        "query": query,
        "total_results": len(items),
        "answer": raw.get("answer", ""),
        "items": items,
    }


async def search(args: argparse.Namespace) -> dict:
    query = args.query
    if args.site:
        query = f"site:{args.site} {query}"

    domains = [args.site] if args.site else None

    ck = cache_key(SOURCE, {"q": query, "n": args.max_results, "d": args.days})
    cached = cache_get(ck)
    if cached is not None:
        return cached

    raw = await _tavily_search(
        query,
        max_results=args.max_results,
        include_domains=domains,
        days=args.days,
    )
    result = _parse_results(raw, query)
    cache_put(ck, result, ttl_seconds=1800)
    return result


def main() -> None:
    if not TAVILY_API_KEY:
        print(
            json.dumps({"error": "TAVILY_API_KEY not set. Add it to .env"}),
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Web search via Tavily")
    parser.add_argument("--query", "-q", required=True, help="Search query")
    parser.add_argument("--site", help="Limit search to a specific domain")
    parser.add_argument("--max-results", type=int, default=10, help="Max results (default 10)")
    parser.add_argument("--days", type=int, help="Limit to results from last N days")
    args = parser.parse_args()

    result = asyncio.run(search(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
