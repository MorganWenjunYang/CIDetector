#!/usr/bin/env python3
"""Execution helpers for CIDector research plans."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_TIMEOUT_SEC = 90
DEFAULT_MAX_WORKERS = 4
PREVIEW_ITEM_LIMIT = 3
PREVIEW_TEXT_LIMIT = 280


def _truncate(text: str, max_len: int = PREVIEW_TEXT_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _is_real_search_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    md = item.get("metadata") or {}
    if md.get("error"):
        return False
    if md.get("fallback") is True:
        return False
    return True


def _build_command(query: str, tool_def: Any, params: dict[str, str]) -> list[str]:
    cmd = [sys.executable, tool_def.script]
    if tool_def.script != "tools/fetch_page.py":
        cmd.extend(["--query", query])
    for key, value in params.items():
        cmd.extend([key, str(value)])
    return cmd


def _count_items(data: dict) -> tuple[int, int]:
    items = data.get("items")
    if not isinstance(items, list):
        return 0, 0
    return len(items), sum(1 for item in items if _is_real_search_item(item))


def _detect_fallback(data: dict) -> bool:
    items = data.get("items")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        md = item.get("metadata") or {}
        if md.get("fallback") or md.get("fallback_source") or md.get("via"):
            return True
    return False


def _normalize_source_label(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain_from_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _requested_source_label(step: dict, tool_def: Any) -> str:
    params = step.get("params", {})
    tool_key = step["tool"]
    if tool_key == "china_trials":
        return f"china_trials:{params.get('--source', 'all')}"
    if tool_key == "conferences":
        return f"conferences:{params.get('--conference', 'all')}"
    if tool_key == "stock_disclosure":
        return f"stock_disclosure:{params.get('--exchange', 'both')}"
    if tool_key == "web_search":
        site = params.get("--site")
        return f"web_search:{site}" if site else "web_search:general"
    if tool_key == "fetch_page":
        return f"fetch_page:{params.get('--url', '')}"
    return tool_def.name


def _extract_transparency(data: dict, requested_source: str) -> dict:
    items = data.get("items")
    actual_sources: list[str] = []
    fallback_reasons: list[str] = []
    domains: list[str] = []

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            md = item.get("metadata") or {}
            has_error = bool(md.get("error"))
            combined_label = None
            if not has_error and md.get("conference") and md.get("via"):
                combined_label = f"{md['conference']} via {md['via']}"
            elif not has_error and md.get("registry") and md.get("fallback_source"):
                combined_label = f"{md['registry']} via fallback"

            if combined_label and combined_label not in actual_sources:
                actual_sources.append(combined_label)

            for key in ("registry", "conference", "exchange", "feed"):
                if has_error:
                    continue
                label = _normalize_source_label(md.get(key))
                if label and label not in actual_sources:
                    actual_sources.append(label)
            fallback_reason = _normalize_source_label(md.get("fallback_source"))
            if fallback_reason and fallback_reason not in fallback_reasons:
                fallback_reasons.append(fallback_reason)
            via = _normalize_source_label(md.get("via"))
            if via:
                via_reason = f"via {via}"
                if via_reason not in fallback_reasons:
                    fallback_reasons.append(via_reason)
            url = str(item.get("url") or "")
            domain = _domain_from_url(url)
            if domain and not has_error and domain not in domains:
                domains.append(domain)

    base_source = _normalize_source_label(data.get("source"))
    if base_source and not actual_sources and base_source not in actual_sources:
        actual_sources.insert(0, base_source)

    if not actual_sources and domains:
        actual_sources = domains[:3]

    used_fallback = _detect_fallback(data)
    source_mismatch = False
    lowered_requested = requested_source.lower()
    if actual_sources:
        source_mismatch = not any(src.lower() in lowered_requested or lowered_requested in src.lower() for src in actual_sources)

    return {
        "requested_source": requested_source,
        "actual_sources": actual_sources,
        "fallback_reasons": fallback_reasons,
        "domains": domains[:5],
        "used_fallback": used_fallback,
        "source_mismatch": source_mismatch and used_fallback,
    }


def _preview_items(data: dict) -> list[dict]:
    items = data.get("items")
    if not isinstance(items, list):
        return []
    real_items = [item for item in items if _is_real_search_item(item)]
    candidates = real_items[:PREVIEW_ITEM_LIMIT] if real_items else items[:PREVIEW_ITEM_LIMIT]
    preview: list[dict] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        entry = {
            "title": str(item.get("title") or "")[:200],
            "url": str(item.get("url") or "")[:400],
        }
        if item.get("published_at"):
            entry["published_at"] = str(item["published_at"])[:80]
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            entry["content_preview"] = _truncate(content.strip())
        preview.append(entry)
    return preview


def execute_plan_step(
    *,
    query: str,
    step: dict,
    tool_def: Any,
    project_root: Path,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Execute a single search-plan step and normalize the result."""
    cmd = _build_command(query, tool_def, step["params"])
    command_str = " ".join(cmd)
    requested_source = _requested_source_label(step, tool_def)
    start = time.monotonic()

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(project_root),
        )
    except subprocess.TimeoutExpired:
        duration = round(time.monotonic() - start, 1)
        return {
            "tool": step["tool"],
            "tool_name": tool_def.name,
            "priority": step["priority"],
            "reason": step["reason"],
            "status": "error",
            "source": None,
            "requested_script": tool_def.script,
            "requested_source": requested_source,
            "command": command_str,
            "duration_sec": duration,
            "item_count": 0,
            "real_item_count": 0,
            "fallback_used": False,
            "actual_sources": [],
            "fallback_reasons": [],
            "source_domains": [],
            "source_mismatch": False,
            "error": f"timeout after {timeout_sec}s",
            "preview": [],
            "data": None,
        }

    duration = round(time.monotonic() - start, 1)
    stderr = (completed.stderr or "").strip()

    if completed.returncode != 0:
        return {
            "tool": step["tool"],
            "tool_name": tool_def.name,
            "priority": step["priority"],
            "reason": step["reason"],
            "status": "error",
            "source": None,
            "requested_script": tool_def.script,
            "requested_source": requested_source,
            "command": command_str,
            "duration_sec": duration,
            "item_count": 0,
            "real_item_count": 0,
            "fallback_used": False,
            "actual_sources": [],
            "fallback_reasons": [],
            "source_domains": [],
            "source_mismatch": False,
            "error": stderr[:400] or f"exit code {completed.returncode}",
            "preview": [],
            "data": None,
        }

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "tool": step["tool"],
            "tool_name": tool_def.name,
            "priority": step["priority"],
            "reason": step["reason"],
            "status": "error",
            "source": None,
            "requested_script": tool_def.script,
            "requested_source": requested_source,
            "command": command_str,
            "duration_sec": duration,
            "item_count": 0,
            "real_item_count": 0,
            "fallback_used": False,
            "actual_sources": [],
            "fallback_reasons": [],
            "source_domains": [],
            "source_mismatch": False,
            "error": f"invalid JSON: {exc}",
            "preview": [],
            "data": None,
        }

    item_count, real_item_count = _count_items(data)
    transparency = _extract_transparency(data, requested_source)
    fallback_used = transparency["used_fallback"]

    status = "success"
    if data.get("error"):
        status = "error"
    elif "items" in data and real_item_count == 0:
        status = "empty"
    elif "content" in data and not data.get("content"):
        status = "empty"

    return {
        "tool": step["tool"],
        "tool_name": tool_def.name,
        "priority": step["priority"],
        "reason": step["reason"],
        "status": status,
        "source": data.get("source"),
        "requested_script": tool_def.script,
        "requested_source": requested_source,
        "command": command_str,
        "duration_sec": duration,
        "item_count": item_count,
        "real_item_count": real_item_count,
        "fallback_used": fallback_used,
        "actual_sources": transparency["actual_sources"],
        "fallback_reasons": transparency["fallback_reasons"],
        "source_domains": transparency["domains"],
        "source_mismatch": transparency["source_mismatch"],
        "error": data.get("error"),
        "preview": _preview_items(data),
        "data": data,
    }


def execute_search_plan(
    *,
    query: str,
    search_plan: list[dict],
    tools: dict[str, Any],
    project_root: Path,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict:
    """Execute a research search plan and return normalized results plus summary."""
    indexed_results: list[tuple[int, dict]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(search_plan)))) as pool:
        futures = {
            pool.submit(
                execute_plan_step,
                query=query,
                step=step,
                tool_def=tools[step["tool"]],
                project_root=project_root,
                timeout_sec=timeout_sec,
            ): idx
            for idx, step in enumerate(search_plan)
        }
        for future in as_completed(futures):
            idx = futures[future]
            indexed_results.append((idx, future.result()))

    results = [result for _, result in sorted(indexed_results, key=lambda item: item[0])]
    summary = {
        "total_steps": len(results),
        "successful_steps": sum(1 for r in results if r["status"] == "success"),
        "empty_steps": sum(1 for r in results if r["status"] == "empty"),
        "errored_steps": sum(1 for r in results if r["status"] == "error"),
        "fallback_steps": sum(1 for r in results if r["fallback_used"]),
        "source_mismatch_steps": sum(1 for r in results if r["source_mismatch"]),
        "total_real_items": sum(r["real_item_count"] for r in results),
    }
    return {
        "query": query,
        "summary": summary,
        "results": results,
    }
