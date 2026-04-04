#!/usr/bin/env python3
"""Search PubMed via NCBI E-utilities (ESearch + EFetch).

Usage:
    python tools/search_pubmed.py --query "B7H4 antibody drug conjugate"
    python tools/search_pubmed.py --query "CLDN18.2" --max-results 10
    python tools/search_pubmed.py --query "pembrolizumab NSCLC" --sort relevance
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from utils.http_client import fetch_json, fetch_text
from utils.cache import cache_key, get as cache_get, put as cache_put

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SOURCE = "PubMed"
RATE_KEY = "pubmed"

NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "")
TOOL_NAME = "CIDector"


def _common_params() -> dict:
    params: dict = {"tool": TOOL_NAME}
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return params


async def _esearch(query: str, max_results: int, sort: str) -> tuple[list[str], int]:
    """Return (list_of_pmids, total_count)."""
    params = {
        **_common_params(),
        "db": "pubmed",
        "term": query,
        "retmax": str(max_results),
        "retmode": "json",
        "sort": sort,
    }
    rate = 10.0 if NCBI_API_KEY else 3.0
    data = await fetch_json(
        f"{EUTILS_BASE}/esearch.fcgi",
        params=params,
        rate_key=RATE_KEY,
        rate_limit=rate,
    )
    result = data.get("esearchresult", {})
    return result.get("idlist", []), int(result.get("count", 0))


def _xml_text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    if child is None:
        return ""
    return (child.text or "").strip()


def _parse_article(article_el: ET.Element) -> dict:
    medline = article_el.find("MedlineCitation")
    if medline is None:
        return {}

    pmid_el = medline.find("PMID")
    pmid = pmid_el.text.strip() if pmid_el is not None and pmid_el.text else ""

    article = medline.find("Article")
    if article is None:
        return {}

    title_el = article.find("ArticleTitle")
    title = "".join(title_el.itertext()).strip() if title_el is not None else ""

    abstract_el = article.find("Abstract")
    abstract_parts: list[str] = []
    if abstract_el is not None:
        for atext in abstract_el.findall("AbstractText"):
            label = atext.get("Label", "")
            text = "".join(atext.itertext()).strip()
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
    abstract = " ".join(abstract_parts)

    journal_el = article.find("Journal")
    journal = ""
    pub_date_str = ""
    if journal_el is not None:
        journal = _xml_text(journal_el, "Title")
        ji = journal_el.find("JournalIssue")
        if ji is not None:
            pd = ji.find("PubDate")
            if pd is not None:
                y = _xml_text(pd, "Year")
                m = _xml_text(pd, "Month")
                d = _xml_text(pd, "Day")
                pub_date_str = "-".join(p for p in [y, m, d] if p)

    author_list = article.find("AuthorList")
    authors: list[str] = []
    if author_list is not None:
        for author in author_list.findall("Author"):
            last = _xml_text(author, "LastName")
            fore = _xml_text(author, "ForeName")
            if last:
                authors.append(f"{last} {fore}".strip())

    doi = ""
    id_list = article_el.find(".//ArticleIdList")
    if id_list is not None:
        for aid in id_list.findall("ArticleId"):
            if aid.get("IdType") == "doi":
                doi = (aid.text or "").strip()
                break

    return {
        "title": title,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "content": abstract,
        "published_at": pub_date_str,
        "metadata": {
            "pmid": pmid,
            "journal": journal,
            "authors": authors[:5],
            "doi": doi,
        },
    }


async def _efetch(pmids: list[str]) -> list[dict]:
    """Fetch article details for a list of PMIDs."""
    if not pmids:
        return []

    rate = 10.0 if NCBI_API_KEY else 3.0
    params = {
        **_common_params(),
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    xml_text = await fetch_text(
        f"{EUTILS_BASE}/efetch.fcgi",
        params=params,
        rate_key=RATE_KEY,
        rate_limit=rate,
    )

    root = ET.fromstring(xml_text)
    items: list[dict] = []
    for article_el in root.findall("PubmedArticle"):
        parsed = _parse_article(article_el)
        if parsed:
            items.append(parsed)
    return items


async def search(args: argparse.Namespace) -> dict:
    ck = cache_key(SOURCE, {"q": args.query, "n": args.max_results, "s": args.sort})
    cached = cache_get(ck)
    if cached is not None:
        return cached

    pmids, total = await _esearch(args.query, args.max_results, args.sort)
    items = await _efetch(pmids)

    result = {
        "source": SOURCE,
        "query": args.query,
        "total_results": total,
        "items": items,
    }
    cache_put(ck, result, ttl_seconds=3600)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Search PubMed")
    parser.add_argument("--query", "-q", required=True, help="Search term")
    parser.add_argument("--max-results", type=int, default=10, help="Max results (default 10)")
    parser.add_argument(
        "--sort",
        default="relevance",
        choices=["relevance", "pub_date"],
        help="Sort order (default: relevance)",
    )
    args = parser.parse_args()

    result = asyncio.run(search(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
