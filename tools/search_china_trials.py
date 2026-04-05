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

from utils.http_client import fetch_text, fetch_text_auto
from utils.parsers import parse_html, extract_text, safe_json_output
from utils.cache import cache_key, get as cache_get, put as cache_put

RATE_KEY = "china_trials"


# ---------------------------------------------------------------------------
# CDE  (www.cde.org.cn)
# ---------------------------------------------------------------------------

async def _search_cde(query: str, max_results: int) -> list[dict]:
    """Search CDE drug information via the NMPA public data search API.

    The CDE main site (cde.org.cn) has very aggressive anti-bot protection.
    We use the NMPA datasearch API as the primary source, which is more accessible.
    """
    items: list[dict] = []
    api_url = "https://www.nmpa.gov.cn/datasearch/search-result.html"

    # Try NMPA API first
    try:
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nmpa.gov.cn/datasearch/home-index.html",
        }

        search_api = "https://www.nmpa.gov.cn/datasearch/search-info.html"
        params = {
            "nmpa": "yp",
            "paramDbId": "",
            "paramStr": query,
            "paramPageNum": "1",
            "paramPageSize": str(max_results),
        }

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(search_api, params=params, headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    result_list = data.get("list", [])
                    for item in result_list[:max_results]:
                        title = item.get("COLUMN2", item.get("column2", ""))
                        detail_id = item.get("ID", item.get("id", ""))
                        if title:  # Only add items with actual content
                            items.append({
                                "title": title,
                                "url": f"https://www.nmpa.gov.cn/datasearch/search-info.html?id={detail_id}",
                                "content": str(item),
                                "published_at": item.get("COLUMN5", ""),
                                "metadata": {"registry": "CDE/NMPA"},
                            })
                except Exception:
                    pass

    except Exception as exc:
        # NMPA API failed, will try fallback below
        pass

    # Fallback: try CDE website directly if NMPA returned no results
    if not items:
        try:
            # Use fetch_text_auto which falls back to Playwright for anti-bot pages
            html = await fetch_text_auto(
                "https://www.cde.org.cn/",
                rate_key=RATE_KEY, rate_limit=2.0, timeout=10,
            )
            soup = parse_html(html)
            q_lower = query.lower()
            for a_tag in soup.select("a[href]"):
                title = extract_text(a_tag)
                href = a_tag.get("href", "")
                if not title or not href or len(title) < 6:
                    continue
                if q_lower and q_lower not in title.lower():
                    continue
                full_url = href if href.startswith("http") else f"https://www.cde.org.cn{href}"
                items.append({
                    "title": title,
                    "url": full_url,
                    "content": "",
                    "published_at": "",
                    "metadata": {"registry": "CDE"},
                })
                if len(items) >= max_results:
                    break
        except Exception as exc:
            # Both NMPA and CDE failed
            items.append({
                "title": f"[CDE search error: {exc}]",
                "url": api_url,
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

    ChiCTR uses Aliyun WAF anti-bot. We use Playwright to fill the search form
    and parse the result table.
    """
    items: list[dict] = []
    search_url = "https://www.chictr.org.cn/searchproj.html"
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="zh-CN",
            )
            page = await ctx.new_page()
            await page.goto(search_url, wait_until="load", timeout=30000)
            await page.wait_for_timeout(2000)

            await page.fill("#topic", query)
            await page.press("#topic", "Enter")
            await page.wait_for_timeout(5000)

            html = await page.content()
            await browser.close()

        soup = parse_html(html)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 4:
                continue
            reg_link = cells[1].find("a") if len(cells) > 1 else None
            title_link = cells[2].find("a") if len(cells) > 2 else None

            reg_no = extract_text(cells[1])
            title = extract_text(cells[2])
            study_type = extract_text(cells[3]) if len(cells) > 3 else ""
            date_str = extract_text(cells[4]) if len(cells) > 4 else ""

            href = ""
            link = title_link or reg_link
            if link:
                href = link.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://www.chictr.org.cn/{href}"

            items.append({
                "title": title or reg_no,
                "url": href,
                "content": f"Registration: {reg_no}. Type: {study_type}.",
                "published_at": date_str,
                "metadata": {"registry": "ChiCTR", "registration_no": reg_no},
            })
            if len(items) >= max_results:
                break

    except Exception as exc:
        items.append({
            "title": f"[ChiCTR search error: {exc}]",
            "url": search_url,
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
