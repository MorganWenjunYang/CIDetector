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

from utils.http_client import fetch_text, fetch_text_auto, fetch_json
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
    primary_error: str | None = None
    try:
        html = await fetch_text_auto(
            url,
            rate_key=RATE_KEY,
            rate_limit=2.0,
            timeout=12,
            max_retries=1,
        )
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
        primary_error = str(exc)

    if not items:
        items = await _search_pubmed_conference_fallback(
            query,
            max_results,
            conference="AACR",
            pubmed_query=f'{query} AND ("AACR"[All Fields] OR "Cancer Res"[Journal] OR "Clin Cancer Res"[Journal])',
            source_url=url,
            primary_error=primary_error,
        )
    return items


# ---------------------------------------------------------------------------
# ASCO (asco.org / abstract.asco.org)
# ---------------------------------------------------------------------------

async def _search_asco(query: str, max_results: int) -> list[dict]:
    """Search ASCO abstracts.

    Primary: try ascopubs.org. Fallback: PubMed search restricted to JCO
    (Journal of Clinical Oncology) where ASCO meeting abstracts are published.
    """
    items: list[dict] = []
    primary_error: str | None = None

    url = f"https://ascopubs.org/action/doSearch?text1={quote(query)}&startPage=0&pageSize={max_results}"
    try:
        html = await fetch_text(url, rate_key=RATE_KEY, rate_limit=2.0, timeout=15, max_retries=1)
        soup = parse_html(html)

        for article in soup.select(
            "div.searchResultItem, article.item, div.issue-item, "
            "div.search-result, li.search-result-item"
        )[:max_results]:
            link = article.find("a", class_="ref nowrap") or article.find("a")
            if not link:
                continue
            title = extract_text(link)
            if not title or len(title) < 5:
                continue
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
        primary_error = str(exc)

    if not items:
        items = await _search_asco_via_pubmed(query, max_results)
        if primary_error:
            for item in items:
                if not isinstance(item, dict):
                    continue
                metadata = item.setdefault("metadata", {})
                if metadata.get("error"):
                    continue
                metadata["primary_source_failed"] = True
                metadata["primary_source_error"] = primary_error[:240]

    return items


async def _search_asco_via_pubmed(query: str, max_results: int) -> list[dict]:
    """Fallback: search PubMed for ASCO abstracts published in JCO."""
    items: list[dict] = []
    try:
        pubmed_query = f"{query} AND (\"J Clin Oncol\"[Journal] OR \"ASCO\"[All Fields])"
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        search_data = await fetch_json(
            search_url,
            params={"db": "pubmed", "term": pubmed_query, "retmax": str(max_results),
                    "retmode": "json", "sort": "pub_date"},
            rate_key="pubmed", rate_limit=3.0,
        )
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return items

        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        summary_data = await fetch_json(
            summary_url,
            params={"db": "pubmed", "id": ",".join(id_list), "retmode": "json"},
            rate_key="pubmed", rate_limit=3.0,
        )
        results = summary_data.get("result", {})
        for pmid in id_list:
            article = results.get(pmid, {})
            if not article or pmid == "uids":
                continue
            title = article.get("title", "")
            authors = ", ".join(a.get("name", "") for a in article.get("authors", [])[:3])
            pub_date = article.get("pubdate", "")
            source = article.get("source", "")
            items.append({
                "title": title,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "content": f"Authors: {authors}. Source: {source}".strip(),
                "published_at": pub_date,
                "metadata": {"conference": "ASCO", "via": "PubMed", "pmid": pmid},
            })
    except Exception as exc:
        items.append({
            "title": f"[ASCO search error: {exc}]",
            "url": "https://ascopubs.org/",
            "content": str(exc),
            "published_at": "",
            "metadata": {"conference": "ASCO", "error": True},
        })
    return items


async def _search_pubmed_conference_fallback(
    query: str,
    max_results: int,
    *,
    conference: str,
    pubmed_query: str,
    source_url: str,
    primary_error: str | None,
) -> list[dict]:
    """Shared PubMed fallback for conferences with brittle official search pages."""
    items: list[dict] = []
    try:
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        search_data = await fetch_json(
            search_url,
            params={
                "db": "pubmed",
                "term": pubmed_query,
                "retmax": str(max_results),
                "retmode": "json",
                "sort": "pub_date",
            },
            rate_key="pubmed",
            rate_limit=3.0,
        )
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return items

        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        summary_data = await fetch_json(
            summary_url,
            params={"db": "pubmed", "id": ",".join(id_list), "retmode": "json"},
            rate_key="pubmed",
            rate_limit=3.0,
        )
        results = summary_data.get("result", {})
        for pmid in id_list:
            article = results.get(pmid, {})
            if not article or pmid == "uids":
                continue
            title = article.get("title", "")
            authors = ", ".join(a.get("name", "") for a in article.get("authors", [])[:3])
            pub_date = article.get("pubdate", "")
            source = article.get("source", "")
            metadata = {
                "conference": conference,
                "via": "PubMed",
                "pmid": pmid,
                "source_url": source_url,
            }
            if primary_error:
                metadata["primary_source_failed"] = True
                metadata["primary_source_error"] = primary_error[:240]
            items.append({
                "title": title,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "content": f"Authors: {authors}. Source: {source}".strip(),
                "published_at": pub_date,
                "metadata": metadata,
            })
    except Exception as exc:
        items.append({
            "title": f"[{conference} search error: {exc}]",
            "url": source_url,
            "content": str(exc),
            "published_at": "",
            "metadata": {"conference": conference, "error": True},
        })
    return items


# ---------------------------------------------------------------------------
# ESMO (esmo.org)
# ---------------------------------------------------------------------------

async def _search_esmo(query: str, max_results: int) -> list[dict]:
    """Search ESMO meeting resources.

    `sources.md` points to the public meeting calendar, which is better suited to
    event discovery than free-text abstract search. We therefore:
    1. Try the official meeting calendar for query hits
    2. Fall back to PubMed (`Ann Oncol` / ESMO mentions) for topic search
    """
    items: list[dict] = []
    url = "https://www.esmo.org/meeting-calendar"
    primary_error: str | None = None
    try:
        html = await fetch_text_auto(
            url,
            rate_key=RATE_KEY,
            rate_limit=2.0,
            timeout=12,
            max_retries=1,
        )
        soup = parse_html(html)
        q_lower = query.lower()
        for link in soup.select("a[href]"):
            title = extract_text(link)
            href = link.get("href", "")
            if not title or len(title) < 6:
                continue
            if q_lower not in title.lower():
                continue
            if href and not href.startswith("http"):
                href = f"https://www.esmo.org{href}"
            items.append({
                "title": title,
                "url": href,
                "content": "Matched from official ESMO meeting calendar.",
                "published_at": "",
                "metadata": {"conference": "ESMO", "source_type": "meeting_calendar"},
            })
            if len(items) >= max_results:
                break
    except Exception as exc:
        primary_error = str(exc)

    if not items:
        items = await _search_pubmed_conference_fallback(
            query,
            max_results,
            conference="ESMO",
            pubmed_query=f'{query} AND ("ESMO"[All Fields] OR "Ann Oncol"[Journal])',
            source_url=url,
            primary_error=primary_error,
        )
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

    requested_conference = conf.upper()
    for item in all_items:
        if not isinstance(item, dict):
            continue
        metadata = item.setdefault("metadata", {})
        metadata.setdefault("requested_conference", requested_conference)

    output = safe_json_output("Conferences", args.query, all_items)

    real_items = [
        item for item in all_items
        if isinstance(item, dict) and not (item.get("metadata") or {}).get("error")
    ]
    has_error_items = any(
        isinstance(item, dict) and (item.get("metadata") or {}).get("error")
        for item in all_items
    )
    has_fallback_items = any(
        isinstance(item, dict) and (
            (item.get("metadata") or {}).get("via")
            or (item.get("metadata") or {}).get("primary_source_failed")
        )
        for item in real_items
    )

    if not real_items:
        pass
    elif has_error_items or has_fallback_items:
        cache_put(ck, output, ttl_seconds=300)
    else:
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
