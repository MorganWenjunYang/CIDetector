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

from utils.http_client import fetch_text_auto, fetch_text_post_auto, _looks_like_antibot
from utils.parsers import parse_html, extract_text, safe_json_output
from utils.cache import cache_key, get as cache_get, put as cache_put

RATE_KEY = "china_trials"
CDE_LIST_URL = "https://www.cde.org.cn/main/xxgk/listpage/9f9c74c73e0f8f56a8bfbc646055026d"
CHINADRUG_INDEX_URL = "https://www.chinadrugtrials.org.cn/index.html"


# ---------------------------------------------------------------------------
# CDE  (www.cde.org.cn)
# ---------------------------------------------------------------------------

async def _search_cde(query: str, max_results: int) -> list[dict]:
    """Search CDE drug information via the NMPA public data search API.

    `sources.md` anchors CDE to the official list page, so we use that as the
    browser-first primary source. Because CDE is frequently protected by anti-bot
    measures, we keep a bounded fallback chain and avoid long multi-hop waits.
    """
    items: list[dict] = []
    primary_failures: list[str] = []

    try:
        items = await asyncio.wait_for(
            _search_cde_official_page(query, max_results),
            timeout=12,
        )
    except Exception as exc:
        primary_failures.append(f"cde_official_page_error:{str(exc)[:120]}")

    # Fallback 1: ChinaDrugTrials official no-login search
    if not items:
        cdth_items = await _search_chinadrugtrials(query, max_results)
        for item in cdth_items:
            if not item.get("metadata", {}).get("error"):
                item["metadata"] = item.get("metadata", {})
                item["metadata"]["fallback_source"] = "ChinaDrugTrials (CDE unavailable)"
                item["metadata"]["requested_registry"] = "CDE"
                item["metadata"]["primary_source_failed"] = True
                if primary_failures:
                    item["metadata"]["primary_source_errors"] = primary_failures[:3]
                items.append(item)

    # Fallback 2: use ChiCTR if all else failed
    if not items:
        chictr_items = await _search_chictr(query, max_results)
        # Filter out error items and mark as fallback
        for item in chictr_items:
            if not item.get("metadata", {}).get("error"):
                item["metadata"] = item.get("metadata", {})
                item["metadata"]["fallback_source"] = "ChiCTR (CDE unavailable)"
                item["metadata"]["requested_registry"] = "CDE"
                item["metadata"]["primary_source_failed"] = True
                if primary_failures:
                    item["metadata"]["primary_source_errors"] = primary_failures[:3]
                items.append(item)

    # Fallback 3: ClinicalTrials.gov with China location filter
    if not items:
        ctgov_items = await _search_clinicaltrials_gov_china(query, max_results)
        for item in ctgov_items:
            if not item.get("metadata", {}).get("error"):
                item["metadata"] = item.get("metadata", {})
                item["metadata"]["fallback_source"] = "ClinicalTrials.gov (China locations)"
                item["metadata"]["requested_registry"] = "CDE"
                item["metadata"]["primary_source_failed"] = True
                if primary_failures:
                    item["metadata"]["primary_source_errors"] = primary_failures[:3]
                items.append(item)

    return items


async def _search_cde_official_page(query: str, max_results: int) -> list[dict]:
    """Search CDE official page using browser automation on the public list page."""
    items: list[dict] = []
    try:
        html = await fetch_text_auto(
            CDE_LIST_URL,
            rate_key=RATE_KEY,
            rate_limit=1.0,
            timeout=10,
            max_retries=1,
        )
        soup = parse_html(html)
        q_lower = query.lower()
        selectors = [
            "a[href*='viewInfoCommon']",
            "a[href*='listpage']",
            ".list a[href]",
            "li a[href]",
            "a[href]",
        ]
        links = []
        for selector in selectors:
            links = soup.select(selector)
            if links:
                break

        for a_tag in links:
            title = extract_text(a_tag)
            href = a_tag.get("href", "")
            if not title or len(title) < 6 or not href:
                continue
            if q_lower not in title.lower():
                continue
            full_url = href if href.startswith("http") else f"https://www.cde.org.cn{href}"
            items.append({
                "title": title,
                "url": full_url,
                "content": "Matched from CDE official disclosure list page.",
                "published_at": "",
                "metadata": {"registry": "CDE", "requested_registry": "CDE", "source_type": "official_list"},
            })
            if len(items) >= max_results:
                break
    except Exception:
        pass
    return items


# ---------------------------------------------------------------------------
# ClinicalTrials.gov with China location filter (fallback)
# ---------------------------------------------------------------------------

async def _search_clinicaltrials_gov_china(query: str, max_results: int) -> list[dict]:
    """Search ClinicalTrials.gov for trials conducted in China.

    This serves as a reliable fallback when all Chinese domestic registries
    (CDE, ChinaDrugTrials, ChiCTR) are inaccessible due to network restrictions
    or anti-bot measures.
    """
    items: list[dict] = []

    try:
        import httpx

        # ClinicalTrials.gov API v2
        api_url = "https://clinicaltrials.gov/api/v2/studies"

        # Build query with China location filter
        full_query = f"AREA[LOCATION_COUNTRY]China+{query}"

        params = {
            "query.cond": full_query,
            "pageSize": str(max_results),
            "countTotal": "true",
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(api_url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                studies = data.get("studies", [])
                for study in studies[:max_results]:
                    protocol = study.get("protocolSection", {})
                    id_module = protocol.get("identificationModule", {})
                    status_module = protocol.get("statusModule", {})
                    design_module = protocol.get("designModule", {})
                    arms_interventions = protocol.get("armsInterventionsModule", {})
                    sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
                    locations_module = protocol.get("locationsModule", {})

                    nct_id = id_module.get("nctId", "")
                    title = id_module.get("briefTitle", "")
                    status = status_module.get("overallStatus", "")
                    phases = design_module.get("phases", [])
                    phase = phases[0] if phases else ""
                    sponsor = sponsor_module.get("leadSponsor", "")

                    # Count China locations
                    locations = locations_module.get("locations", [])
                    china_count = sum(1 for loc in locations if loc.get("country", "") == "China")

                    # Get intervention names
                    interventions_list = arms_interventions.get("interventions", [])
                    intervention_names = [i.get("name", "") for i in interventions_list[:3]]

                    items.append({
                        "title": title,
                        "url": f"https://clinicaltrials.gov/study/{nct_id}",
                        "content": f"Sponsor: {sponsor}. Phase: {phase}. Status: {status}. China locations: {china_count}.",
                        "published_at": status_module.get("startDateStruct", {}).get("month", ""),
                        "metadata": {
                            "registry": "ClinicalTrials.gov",
                            "nct_id": nct_id,
                            "phase": phase,
                            "status": status,
                            "sponsor": sponsor,
                            "china_locations": china_count,
                        },
                    })

    except Exception:
        # Return empty on error - this is just a fallback
        pass

    return items


# ---------------------------------------------------------------------------
# ChinaDrugTrials (www.chinadrugtrials.org.cn)
# ---------------------------------------------------------------------------

async def _search_chinadrugtrials(query: str, max_results: int) -> list[dict]:
    """Search ChinaDrugTrials public registry.

    `sources.md` notes the site supports public no-login search. Use the public
    homepage in a browser first, then fall back to the older form POST flow.
    """
    items: list[dict] = []
    try:
        items = await asyncio.wait_for(
            _search_chinadrugtrials_browser(query, max_results),
            timeout=14,
        )
    except Exception:
        pass

    if items:
        return items

    try:
        html = await fetch_text_post_auto(
            "https://www.chinadrugtrials.org.cn/clinicaltrials.searchlistdetail.dhtml",
            data={
                "currentpage": "1",
                "pagesize": str(max_results),
                "keywords": query,
                "rule": "CTR",
            },
            rate_key=RATE_KEY,
            rate_limit=1.0,
            timeout=10,
            max_retries=1,
        )
        if _looks_like_antibot(html):
            raise RuntimeError("anti-bot page detected")
        items = _parse_chinadrugtrials_results(html, max_results)
    except Exception:
        pass

    if items:
        return items

    ctgov_items = await _search_clinicaltrials_gov_china(query, max_results)
    for item in ctgov_items:
        if item.get("metadata", {}).get("error"):
            continue
        item["metadata"]["fallback_source"] = "ClinicalTrials.gov (China locations)"
        item["metadata"]["requested_registry"] = "ChinaDrugTrials"
    return ctgov_items


def _parse_chinadrugtrials_results(html: str, max_results: int) -> list[dict]:
    items: list[dict] = []
    soup = parse_html(html)
    rows = soup.select("table tr") or soup.select("tr")
    for row in rows[1:]:
        cells = row.select("td")
        if len(cells) < 2:
            continue
        reg_no = extract_text(cells[0])
        title = extract_text(cells[1])
        drug = extract_text(cells[2]) if len(cells) > 2 else ""
        indication = extract_text(cells[3]) if len(cells) > 3 else ""
        link_tag = row.find("a", href=True)
        href = ""
        if link_tag:
            href = link_tag["href"]
            if not href.startswith("http"):
                href = f"https://www.chinadrugtrials.org.cn/{href.lstrip('./')}"
        if title or reg_no:
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
        if len(items) >= max_results:
            break
    return items


async def _search_chinadrugtrials_browser(query: str, max_results: int) -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = await ctx.new_page()
        try:
            await page.goto(CHINADRUG_INDEX_URL, wait_until="load", timeout=15000)
            await page.wait_for_timeout(2500)
            if _looks_like_antibot(await page.content()):
                raise RuntimeError("anti-bot page detected")

            input_selectors = [
                '#keywords',
                'input[name="keywords"]',
                'input[name="keyword"]',
                'input[type="text"]',
            ]
            filled = False
            for selector in input_selectors:
                try:
                    await page.fill(selector, query)
                    filled = True
                    break
                except Exception:
                    continue
            if not filled:
                raise RuntimeError("search input not found")

            clicked = False
            for selector in [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("搜索")',
                'button:has-text("查询")',
                'a:has-text("搜索")',
            ]:
                try:
                    await page.locator(selector).first.click(timeout=3000)
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                await page.keyboard.press("Enter")

            await page.wait_for_timeout(5000)
            html = await page.content()
        finally:
            await ctx.close()
            await browser.close()

    return _parse_chinadrugtrials_results(html, max_results)


# ---------------------------------------------------------------------------
# ChiCTR (www.chictr.org.cn)
# ---------------------------------------------------------------------------

async def _search_chictr(query: str, max_results: int) -> list[dict]:
    """Search ChiCTR (Chinese Clinical Trial Registry).

    ChiCTR uses Aliyun WAF anti-bot. We use Playwright to attempt the search,
    but the verification slider may still block us.
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
            await page.wait_for_timeout(3000)

            # Check for anti-bot verification page
            content = await page.content()
            if "Access Verification" in content or "slide to verify" in content.lower():
                # Try to wait for verification to complete (sometimes it's automatic)
                await page.wait_for_timeout(5000)
                content = await page.content()

            # If still blocked, return empty
            if "Access Verification" in content or "slide to verify" in content.lower():
                await browser.close()
                # Return empty - this is a known limitation
                return []

            # Try multiple possible selectors for the search box
            search_selectors = ["#topic", 'input[name="topic"]', 'input[placeholder*="搜索"]', 'input[type="text"]']
            filled = False
            for selector in search_selectors:
                try:
                    await page.fill(selector, query)
                    filled = True
                    break
                except Exception:
                    continue

            if not filled:
                await browser.close()
                return []

            await page.press("#topic", "Enter") if await page.query_selector("#topic") else await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            html = await page.content()
            await browser.close()

            # Check if we got results or still blocked
            if "Access Verification" in html:
                return []

        soup = parse_html(html)
        # Try multiple table selectors
        rows = soup.select("table tr") or soup.select("table.table tr") or soup.select(".list tr")
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

            if title or reg_no:  # Only add items with actual content
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
        # Return empty on error - don't pollute results with error items
        pass
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

    requested_registry = source.upper()
    for item in all_items:
        if not isinstance(item, dict):
            continue
        metadata = item.setdefault("metadata", {})
        metadata.setdefault("requested_registry", requested_registry)

    output = safe_json_output("ChinaTrials", args.query, all_items)

    # Use shorter TTL for empty results or fallback-only results
    # This allows the cache to expire quickly when upstream sources recover
    has_real_items = any(not item.get("metadata", {}).get("error") for item in all_items)
    has_error_items = any(item.get("metadata", {}).get("error") for item in all_items)
    has_fallback_only = all(item.get("metadata", {}).get("fallback_source") for item in all_items if not item.get("metadata", {}).get("error"))

    if not has_real_items:
        # Don't cache completely empty results - they're likely transient failures
        pass
    elif has_error_items or has_fallback_only:
        # Fallback results (ChiCTR when CDE is down) - cache for 5 minutes only
        cache_put(ck, output, ttl_seconds=300)
    else:
        # Normal results - cache for 1 hour
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
