#!/usr/bin/env python3
"""Fetch a single web page and return its cleaned text content.

Usage:
    python tools/fetch_page.py --url "https://www.fiercebiotech.com/some-article"
    python tools/fetch_page.py --url "https://..." --format markdown
    python tools/fetch_page.py --url "https://..." --dynamic   # uses Playwright
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.parsers import parse_html, extract_text


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def _fetch_static(url: str) -> str:
    import httpx

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


async def _fetch_dynamic(url: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            return await page.content()
        finally:
            await browser.close()


def _select_content_root(html: str):
    soup = parse_html(html)
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    article = soup.find("article")
    if article is not None:
        return article, "article"
    main = soup.find("main")
    if main is not None:
        return main, "main"
    body = soup.find("body")
    return body, "body"


def _html_to_text(html: str) -> tuple[str, str]:
    article, extractor = _select_content_root(html)
    text = extract_text(article)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), extractor


def _html_to_markdown(html: str) -> tuple[str, str]:
    """Simple conversion: headings, paragraphs, links."""
    article, extractor = _select_content_root(html)
    if article is None:
        return "", "none"

    parts: list[str] = []
    for el in article.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        tag_name = el.name
        text = extract_text(el)
        if not text:
            continue
        if tag_name == "h1":
            parts.append(f"# {text}")
        elif tag_name == "h2":
            parts.append(f"## {text}")
        elif tag_name == "h3":
            parts.append(f"### {text}")
        elif tag_name == "h4":
            parts.append(f"#### {text}")
        elif tag_name == "li":
            parts.append(f"- {text}")
        else:
            parts.append(text)

    return "\n\n".join(parts), extractor


async def fetch(args: argparse.Namespace) -> dict:
    fetch_mode = "dynamic" if args.dynamic else "static"
    if args.dynamic:
        html = await _fetch_dynamic(args.url)
    else:
        html = await _fetch_static(args.url)

    extractor = "raw_html"
    if args.format == "html":
        content = html
        content_length = len(content)
    elif args.format == "markdown":
        content, extractor = _html_to_markdown(html)
        content_length = len(content)
    else:
        content, extractor = _html_to_text(html)
        content_length = len(content)

    max_len = 15000
    truncated = False
    if len(content) > max_len:
        content = content[:max_len] + "\n\n... [truncated]"
        truncated = True

    return {
        "source": "fetch_page",
        "url": args.url,
        "format": args.format,
        "content": content,
        "metadata": {
            "fetch_mode": fetch_mode,
            "dynamic_used": args.dynamic,
            "extractor": extractor,
            "content_length": content_length,
            "truncated": truncated,
            "looks_article_like": extractor in {"article", "main"},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a web page")
    parser.add_argument("--url", required=True, help="URL to fetch")
    parser.add_argument(
        "--format",
        default="text",
        choices=["text", "markdown", "html"],
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="Use Playwright for JS-rendered pages",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(fetch(args))
    except Exception as exc:
        result = {
            "source": "fetch_page",
            "url": args.url,
            "format": args.format,
            "content": "",
            "error": str(exc),
            "metadata": {
                "fetch_mode": "dynamic" if args.dynamic else "static",
                "dynamic_used": args.dynamic,
            },
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
