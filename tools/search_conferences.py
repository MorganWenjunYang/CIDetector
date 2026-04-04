#!/usr/bin/env python3
"""Search biomedical conference abstracts (AACR, ASCO, ESMO).

Usage:
    python tools/search_conferences.py --query "B7H4 ADC"
    python tools/search_conferences.py --query "pembrolizumab" --conference asco
    python tools/search_conferences.py --query "CLDN18.2" --conference aacr
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.http_client import fetch_text
from utils.parsers import parse_html, extract_text, safe_json_output
from utils.cache import cache_key, get as cache_get, put as cache_put

RATE_KEY = "conferences"


# ---------------------------------------------------------------------------
# AACR (aacrjournals.org)
# ---------------------------------------------------------------------------

async def _search_aacr(query: str, max_results: int) -> list[dict]:
    """Search AACR journals / abstract archive."""
    items: list[dict] = []
    url = f"https://aacrjournals.org/search-results?page=1&q={quote(query)}&SearchSourceType=1&fl_SiteID=5"
    try:
        html = await fetch_text(url, rate_key=RATE_KEY, rate_limit=2.0, timeout=20)
        soup = parse_html(html)

        for result_el in soup.select("div.sr-list div.al-citation-list-group, div.highwire-cite-metadata, li.search-result")[:max_results]:
            link = result_el.find("a")
            if not link:
                continue
            title = extract_text(link)
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://aacrjournals.org{href}"

            authors_el = result_el.find(class_="highwire-cite-authors")
            authors = extract_text(authors_el) if authors_el else ""

            meta_el = result_el.find(class_="highwire-cite-metadata")
            meta_text = extract_text(meta_el) if meta_el else ""

            items.append({
                "title": title,
                "url": href,
                "content": f"Authors: {authors}. {meta_text}".strip(),
                "published_at": "",
                "metadata": {"conference": "AACR"},
            })
    except Exception as exc:
        items.append({
            "title": f"[AACR search error: {exc}]",
            "url": url,
            "content": str(exc),
            "published_at": "",
            "metadata": {"conference": "AACR", "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# ASCO (asco.org / abstract.asco.org)
# ---------------------------------------------------------------------------

async def _search_asco(query: str, max_results: int) -> list[dict]:
    """Search ASCO abstracts."""
    items: list[dict] = []
    url = f"https://ascopubs.org/action/doSearch?text1={quote(query)}&startPage=0&pageSize={max_results}"
    try:
        html = await fetch_text(url, rate_key=RATE_KEY, rate_limit=2.0, timeout=20)
        soup = parse_html(html)

        for article in soup.select("div.searchResultItem, article.item, div.issue-item")[:max_results]:
            link = article.find("a", class_="ref nowrap") or article.find("a")
            if not link:
                continue
            title = extract_text(link)
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://ascopubs.org{href}"

            authors_el = article.find(class_="contrib")
            authors = extract_text(authors_el) if authors_el else ""

            date_el = article.find(class_="pub-date")
            date_str = extract_text(date_el) if date_el else ""

            items.append({
                "title": title,
                "url": href,
                "content": f"Authors: {authors}".strip(),
                "published_at": date_str,
                "metadata": {"conference": "ASCO"},
            })
    except Exception as exc:
        items.append({
            "title": f"[ASCO search error: {exc}]",
            "url": url,
            "content": str(exc),
            "published_at": "",
            "metadata": {"conference": "ASCO", "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# ESMO (esmo.org)
# ---------------------------------------------------------------------------

async def _search_esmo(query: str, max_results: int) -> list[dict]:
    """Search ESMO oncology abstracts / meeting resources."""
    items: list[dict] = []
    url = f"https://www.esmo.org/search?q={quote(query)}"
    try:
        html = await fetch_text(url, rate_key=RATE_KEY, rate_limit=2.0, timeout=20)
        soup = parse_html(html)

        for result in soup.select("div.search-result, li.search-item, div.result-item")[:max_results]:
            link = result.find("a")
            if not link:
                continue
            title = extract_text(link)
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://www.esmo.org{href}"

            snippet_el = result.find(class_="snippet") or result.find("p")
            snippet = extract_text(snippet_el) if snippet_el else ""

            items.append({
                "title": title,
                "url": href,
                "content": snippet,
                "published_at": "",
                "metadata": {"conference": "ESMO"},
            })
    except Exception as exc:
        items.append({
            "title": f"[ESMO search error: {exc}]",
            "url": url,
            "content": str(exc),
            "published_at": "",
            "metadata": {"conference": "ESMO", "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def search(args: argparse.Namespace) -> dict:
    ck = cache_key("conferences", {"q": args.query, "c": args.conference, "n": args.max_results})
    cached = cache_get(ck)
    if cached is not None:
        return cached

    tasks = []
    conf = args.conference.lower()
    if conf in ("aacr", "all"):
        tasks.append(_search_aacr(args.query, args.max_results))
    if conf in ("asco", "all"):
        tasks.append(_search_asco(args.query, args.max_results))
    if conf in ("esmo", "all"):
        tasks.append(_search_esmo(args.query, args.max_results))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_items: list[dict] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
        elif isinstance(r, Exception):
            all_items.append({
                "title": f"[Error: {r}]",
                "url": "",
                "content": str(r),
                "published_at": "",
                "metadata": {"error": True},
            })

    output = safe_json_output("Conferences", args.query, all_items)
    cache_put(ck, output, ttl_seconds=3600)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Search conference abstracts")
    parser.add_argument("--query", "-q", required=True, help="Search term")
    parser.add_argument(
        "--conference",
        default="all",
        choices=["aacr", "asco", "esmo", "all"],
        help="Which conference (default: all)",
    )
    parser.add_argument("--max-results", type=int, default=10, help="Max results per conference")
    args = parser.parse_args()

    result = asyncio.run(search(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
