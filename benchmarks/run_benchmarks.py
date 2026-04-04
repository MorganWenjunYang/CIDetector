#!/usr/bin/env python3
"""Preliminary benchmark: verify every data source can fetch real data.

Usage:
    python benchmarks/run_benchmarks.py
    python benchmarks/run_benchmarks.py --cases benchmarks/benchmark_cases.yaml
    python benchmarks/run_benchmarks.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_FILE = Path(__file__).resolve().parent / "benchmark_cases.yaml"
TIMEOUT_SEC = 60


def load_cases(path: Path) -> list[dict]:
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


def check_env(requires: list[str]) -> str | None:
    """Return the first missing env var name, or None if all present."""
    for var in requires:
        if not os.environ.get(var):
            return var
    return None


def validate_search_output(data: dict) -> str | None:
    """Validate standard search tool output. Returns error message or None."""
    if "source" not in data:
        return "missing 'source' field"
    items = data.get("items")
    if items is None:
        return "missing 'items' field"
    if not isinstance(items, list):
        return f"'items' is {type(items).__name__}, expected list"
    real_items = [
        i for i in items if not i.get("metadata", {}).get("error")
    ]
    if len(real_items) == 0:
        error_msgs = [
            i.get("title", "") for i in items if i.get("metadata", {}).get("error")
        ]
        detail = "; ".join(error_msgs[:2]) if error_msgs else ""
        return f"0 real items out of {len(items)} total. {detail}".strip()
    return None


def validate_fetch_page_output(data: dict) -> str | None:
    """Validate fetch_page.py output. Returns error message or None."""
    if "source" not in data:
        return "missing 'source' field"
    content = data.get("content")
    if not content:
        return "empty or missing 'content' field"
    return None


def run_case(case: dict, verbose: bool = False) -> dict:
    name = case["name"]
    tool = case["tool"]
    args = case.get("args", [])
    requires_env = case.get("requires_env", [])
    check_mode = case.get("check_mode", "search")
    fragile = case.get("fragile", False)

    missing_env = check_env(requires_env)
    if missing_env:
        return {"name": name, "status": "SKIP", "error": f"{missing_env} not set", "duration_sec": 0}

    cmd = [sys.executable, tool] + args
    if verbose:
        print(f"  Running: {' '.join(cmd)}", file=sys.stderr)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        status = "WARN" if fragile else "FAIL"
        return {"name": name, "status": status, "error": f"timeout after {TIMEOUT_SEC}s", "duration_sec": round(duration, 1)}
    duration = time.monotonic() - start

    if result.returncode != 0:
        stderr_snippet = result.stderr.strip()[:300]
        status = "WARN" if fragile else "FAIL"
        return {"name": name, "status": status, "error": f"exit code {result.returncode}: {stderr_snippet}", "duration_sec": round(duration, 1)}

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        stdout_snippet = result.stdout.strip()[:200]
        status = "WARN" if fragile else "FAIL"
        return {"name": name, "status": status, "error": f"invalid JSON: {e}. stdout starts with: {stdout_snippet}", "duration_sec": round(duration, 1)}

    if check_mode == "fetch_page":
        err = validate_fetch_page_output(data)
    else:
        err = validate_search_output(data)

    if err:
        status = "WARN" if fragile else "FAIL"
        return {"name": name, "status": status, "error": err, "duration_sec": round(duration, 1)}

    return {"name": name, "status": "PASS", "duration_sec": round(duration, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CIDector benchmark suite")
    parser.add_argument(
        "--cases",
        default=str(DEFAULT_CASES_FILE),
        help="Path to benchmark cases YAML (default: benchmarks/benchmark_cases.yaml)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress to stderr")
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    if not cases:
        print(json.dumps({"error": "no benchmark cases found"}))
        sys.exit(1)

    results: list[dict] = []
    for case in cases:
        if args.verbose:
            print(f"[{case['name']}] ...", end="", file=sys.stderr, flush=True)
        r = run_case(case, verbose=args.verbose)
        results.append(r)
        if args.verbose:
            status_icon = {"PASS": " PASS", "FAIL": " FAIL", "WARN": " WARN", "SKIP": " SKIP"}[r["status"]]
            suffix = f" ({r['duration_sec']}s)" if r["duration_sec"] else ""
            err_msg = f" - {r['error']}" if r.get("error") else ""
            print(f" {status_icon}{suffix}{err_msg}", file=sys.stderr)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    warned = sum(1 for r in results if r["status"] == "WARN")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "skipped": skipped,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
