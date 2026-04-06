#!/usr/bin/env python3
"""RSS feed monitor for ongoing competitive intelligence tracking.

Usage:
    python tools/rss_monitor.py --keyword "B7H4"
    python tools/rss_monitor.py --keyword "ADC" --feeds fierce,endpoints,prn
    python tools/rss_monitor.py --list-feeds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.http_client import fetch_text
from utils.parsers import safe_json_output

FEEDS: dict[str, dict] = {
    "fierce": {
        "name": "Fierce Biotech",
        "url": "https://www.fiercebiotech.com/rss/xml",
        "fallback_urls": [
            "https://www.fiercebiotech.com/rss/biotech/xml",
        ],
    },
    "endpoints": {
        "name": "Endpoints News",
        "url": "https://endpts.com/feed/",
    },
    "prn": {
        "name": "PR Newswire - Health",
        "url": "https://www.prnewswire.com/rss/health-latest-news/health-latest-news-list.rss",
    },
}


def _match(keyword: str, text: str) -> bool:
    return bool(re.search(re.escape(keyword), text, re.IGNORECASE))


async def _fetch_feed(key: str, feed_info: dict, keyword: str) -> list[dict]:
    import feedparser

    items: list[dict] = []
    urls_to_try = [feed_info["url"]] + feed_info.get("fallback_urls", [])
    last_exc: Exception | None = None

    for url in urls_to_try:
        try:
            xml = await fetch_text(url, timeout=15)
            parsed = feedparser.parse(xml)
            if not parsed.entries:
                continue

            for entry in parsed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                published = entry.get("published", "")

                if keyword and not (_match(keyword, title) or _match(keyword, summary)):
                    continue

                items.append({
                    "title": title,
                    "url": link,
                    "content": summary[:500],
                    "published_at": published,
                    "metadata": {"feed": feed_info["name"]},
                })
            return items
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc is not None:
        items.append({
            "title": f"[{feed_info['name']} feed error: {last_exc}]",
            "url": feed_info["url"],
            "content": str(last_exc),
            "published_at": "",
            "metadata": {"feed": feed_info["name"], "error": True},
        })
    return items


async def monitor(args: argparse.Namespace) -> dict:
    feed_keys = [f.strip() for f in args.feeds.split(",")]
    invalid_feed_keys = [key for key in feed_keys if key and key not in FEEDS]
    tasks = []
    for key in feed_keys:
        if key in FEEDS:
            tasks.append(_fetch_feed(key, FEEDS[key], args.keyword))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_items: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
        elif isinstance(r, Exception):
            all_items.append({
                "title": f"[RSS monitor error: {r}]",
                "url": "",
                "content": str(r),
                "published_at": "",
                "metadata": {"error": True},
            })

    for key in invalid_feed_keys:
        all_items.append({
            "title": f"[Unknown RSS feed: {key}]",
            "url": "",
            "content": f"Unknown feed key '{key}'. Valid values: {', '.join(sorted(FEEDS))}.",
            "published_at": "",
            "metadata": {"error": True, "invalid_feed": key},
        })

    return safe_json_output("RSS Monitor", args.keyword, all_items)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor RSS feeds for keywords")
    parser.add_argument("--keyword", "-k", default="", help="Keyword to filter (empty = all items)")
    parser.add_argument(
        "--feeds",
        default="fierce,endpoints,prn",
        help="Comma-separated feed keys (default: fierce,endpoints,prn)",
    )
    parser.add_argument("--list-feeds", action="store_true", help="List available feeds")
    args = parser.parse_args()

    if args.list_feeds:
        for key, info in FEEDS.items():
            print(f"  {key:12s}  {info['name']:25s}  {info['url']}")
        return

    selected_keys = [f.strip() for f in args.feeds.split(",") if f.strip()]
    if not selected_keys:
        raise SystemExit("--feeds must include at least one feed key")

    result = asyncio.run(monitor(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
