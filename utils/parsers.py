"""HTML / XML parsing helpers shared across tool scripts."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def extract_text(element: Tag | None, strip: bool = True) -> str:
    if element is None:
        return ""
    text = element.get_text(separator=" ")
    if strip:
        text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_table_rows(
    html: str, table_selector: str = "table"
) -> list[list[str]]:
    """Return a list-of-lists representing a <table> in *html*."""
    soup = parse_html(html)
    table = soup.select_one(table_selector)
    if table is None:
        return []
    rows: list[list[str]] = []
    for tr in table.select("tr"):
        cells = [extract_text(td) for td in tr.select("td, th")]
        if cells:
            rows.append(cells)
    return rows


def clean_text(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text).strip()


def safe_json_output(
    source: str,
    query: str,
    items: list[dict[str, Any]],
    total: int | None = None,
) -> dict[str, Any]:
    """Build the standard JSON output envelope used by all tools."""
    return {
        "source": source,
        "query": query,
        "total_results": total if total is not None else len(items),
        "items": items,
    }
