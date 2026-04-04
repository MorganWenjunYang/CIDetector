#!/usr/bin/env python3
"""Search Chinese clinical trial registries: CDE, ChinaDrugTrials, ChiCTR.

Usage:
    python tools/search_china_trials.py --query "B7H4"
    python tools/search_china_trials.py --query "PD-1" --source chictr
    python tools/search_china_trials.py --query "百利天恒" --source cde
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
from utils.parsers import parse_html, extract_text, safe_json_output
from utils.cache import cache_key, get as cache_get, put as cache_put

RATE_KEY = "china_trials"


# ---------------------------------------------------------------------------
# CDE  (www.cde.org.cn)
# ---------------------------------------------------------------------------

async def _search_cde(query: str, max_results: int) -> list[dict]:
    """Search CDE's public information disclosure pages.

    CDE uses server-side rendered pages with an internal search API.
    We hit the full-text search endpoint and parse the HTML results.
    """
    url = "https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d"
    items: list[dict] = []
    try:
        html = await fetch_text(url, rate_key=RATE_KEY, rate_limit=2.0, timeout=20)
        soup = parse_html(html)
        for a_tag in soup.select("a[href]")[:max_results]:
            title = extract_text(a_tag)
            href = a_tag.get("href", "")
            if title and href:
                full_url = href if href.startswith("http") else f"https://www.cde.org.cn{href}"
                items.append({
                    "title": title,
                    "url": full_url,
                    "content": "",
                    "published_at": "",
                    "metadata": {"registry": "CDE"},
                })
    except Exception as exc:
        items.append({
            "title": f"[CDE search error: {exc}]",
            "url": url,
            "content": str(exc),
            "published_at": "",
            "metadata": {"registry": "CDE", "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# ChinaDrugTrials (www.chinadrugtrials.org.cn)
# ---------------------------------------------------------------------------

async def _search_chinadrugtrials(query: str, max_results: int) -> list[dict]:
    """Search ChinaDrugTrials public registry.

    The site uses form-based search.  We POST the search form and parse results.
    """
    search_url = "https://www.chinadrugtrials.org.cn/clinicaltrials.searchlistdetail.dhtml"
    items: list[dict] = []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.post(
                "https://www.chinadrugtrials.org.cn/clinicaltrials.searchlist.dhtml",
                data={
                    "currentpage": "1",
                    "pagesize": str(max_results),
                    "keywords": query,
                    "rule": "CTR",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                },
            )
            resp.raise_for_status()
            html = resp.text

        soup = parse_html(html)
        rows = soup.select("table tr")
        for row in rows[1: max_results + 1]:
            cells = row.select("td")
            if len(cells) >= 4:
                reg_no = extract_text(cells[0])
                title = extract_text(cells[1])
                drug = extract_text(cells[2])
                indication = extract_text(cells[3])
                link_tag = cells[0].find("a")
                href = ""
                if link_tag and link_tag.get("href"):
                    href = link_tag["href"]
                    if not href.startswith("http"):
                        href = f"https://www.chinadrugtrials.org.cn/{href}"
                items.append({
                    "title": title or reg_no,
                    "url": href,
                    "content": f"Drug: {drug}. Indication: {indication}.",
                    "published_at": "",
                    "metadata": {
                        "registry": "ChinaDrugTrials",
                        "registration_no": reg_no,
                        "drug": drug,
                        "indication": indication,
                    },
                })
    except Exception as exc:
        items.append({
            "title": f"[ChinaDrugTrials search error: {exc}]",
            "url": "https://www.chinadrugtrials.org.cn/",
            "content": str(exc),
            "published_at": "",
            "metadata": {"registry": "ChinaDrugTrials", "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# ChiCTR (www.chictr.org.cn)
# ---------------------------------------------------------------------------

async def _search_chictr(query: str, max_results: int) -> list[dict]:
    """Search ChiCTR (Chinese Clinical Trial Registry).

    The site provides a search page at searchproj.html.
    """
    items: list[dict] = []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.chictr.org.cn/searchproj.html",
                params={"title": query, "officialname": "", "subjectid": ""},
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                },
            )
            resp.raise_for_status()
            html = resp.text

        soup = parse_html(html)
        result_items = soup.select("div.result-item, table.table tr, div.searchResult li")

        for el in result_items[:max_results]:
            link = el.find("a")
            if not link:
                continue
            title = extract_text(link)
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://www.chictr.org.cn/{href}"

            date_el = el.find(class_=re.compile(r"date|time"))
            date_str = extract_text(date_el) if date_el else ""

            items.append({
                "title": title,
                "url": href,
                "content": extract_text(el),
                "published_at": date_str,
                "metadata": {"registry": "ChiCTR"},
            })
    except Exception as exc:
        items.append({
            "title": f"[ChiCTR search error: {exc}]",
            "url": "https://www.chictr.org.cn/searchproj.html",
            "content": str(exc),
            "published_at": "",
            "metadata": {"registry": "ChiCTR", "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def search(args: argparse.Namespace) -> dict:
    ck = cache_key("china_trials", {"q": args.query, "s": args.source, "n": args.max_results})
    cached = cache_get(ck)
    if cached is not None:
        return cached

    tasks = []
    source = args.source.lower()
    if source in ("cde", "all"):
        tasks.append(_search_cde(args.query, args.max_results))
    if source in ("chinadrugtrials", "all"):
        tasks.append(_search_chinadrugtrials(args.query, args.max_results))
    if source in ("chictr", "all"):
        tasks.append(_search_chictr(args.query, args.max_results))

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

    output = safe_json_output("ChinaTrials", args.query, all_items)
    cache_put(ck, output, ttl_seconds=3600)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Search Chinese clinical trial registries")
    parser.add_argument("--query", "-q", required=True, help="Search term")
    parser.add_argument(
        "--source",
        default="all",
        choices=["cde", "chinadrugtrials", "chictr", "all"],
        help="Which registry to search (default: all)",
    )
    parser.add_argument("--max-results", type=int, default=10, help="Max results per source")
    args = parser.parse_args()

    result = asyncio.run(search(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
