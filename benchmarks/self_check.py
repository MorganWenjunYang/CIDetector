#!/usr/bin/env python3
"""Run a small post-install self-check for core CIDector capabilities."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from benchmarks.run_benchmarks import DEFAULT_CASES_FILE, load_cases, run_case

load_dotenv(PROJECT_ROOT / ".env")


CORE_CASE_NAMES = [
    "ClinicalTrials.gov",
    "PubMed",
    "WebSearch (General)",
    "China Trials (CDE)",
    "Conferences (ASCO)",
    "Fetch Page",
]


def build_environment_readiness() -> list[dict]:
    checks: list[dict] = []

    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    checks.append({
        "name": "Tavily API key",
        "status": "READY" if tavily_key else "MISSING",
        "detail": "已配置 TAVILY_API_KEY" if tavily_key else "未配置 TAVILY_API_KEY，新闻/BD/事实核查能力会受限",
    })

    ncbi_email = os.environ.get("NCBI_EMAIL", "")
    checks.append({
        "name": "NCBI email",
        "status": "READY" if ncbi_email else "OPTIONAL",
        "detail": "已配置 NCBI_EMAIL" if ncbi_email else "未配置 NCBI_EMAIL，PubMed 仍可用但限速更严格",
    })

    playwright_installed = subprocess.run(
        [sys.executable, "-c", "import playwright"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    ).returncode == 0
    checks.append({
        "name": "Playwright package",
        "status": "READY" if playwright_installed else "MISSING",
        "detail": "Playwright Python 包可用" if playwright_installed else "未安装 Playwright，动态页面和部分中国源可能不可用",
    })

    chromium_ready = False
    if playwright_installed:
        chromium_ready = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from playwright.sync_api import sync_playwright;"
                    "p=sync_playwright().start();"
                    "browser=p.chromium.launch(headless=True);"
                    "browser.close();"
                    "p.stop()"
                ),
            ],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        ).returncode == 0
    checks.append({
        "name": "Chromium browser",
        "status": "READY" if chromium_ready else "MISSING",
        "detail": "Chromium 可启动" if chromium_ready else "Chromium 未就绪，请运行 `python3 -m playwright install chromium`",
    })

    return checks


def select_core_cases(path: Path) -> list[dict]:
    cases = load_cases(path)
    selected = [case for case in cases if case["name"] in CORE_CASE_NAMES]
    selected.sort(key=lambda case: CORE_CASE_NAMES.index(case["name"]))
    return selected


def build_hints(results: list[dict]) -> list[str]:
    hints: list[str] = []
    for result in results:
        name = result["name"]
        error = result.get("error", "")
        status = result["status"]
        if status == "SKIP" and "TAVILY_API_KEY" in error:
            hints.append("`WebSearch (General)` 被跳过：请在 `.env` 中配置 `TAVILY_API_KEY`。")
        elif name == "China Trials (CDE)" and status in {"WARN", "FAIL"}:
            hints.append("中国源检查未通过：这通常是站点反爬或 Playwright/browser 未准备好。")
        elif name == "Conferences (ASCO)" and status in {"WARN", "FAIL"}:
            hints.append("会议源检查未通过：ASCO 站点波动较大，必要时会依赖 PubMed fallback。")
        elif name == "Fetch Page" and status in {"WARN", "FAIL"}:
            hints.append("通用抓取器检查未通过：如果需要动态页面，请确认 Playwright 和 Chromium 可用。")
    return hints


def render_text(report: dict) -> str:
    lines = [
        "CIDector Self-Check",
        f"Timestamp: {report['timestamp']}",
        "",
        "Environment readiness:",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in report.get("environment", []):
        lines.append(f"| {check['name']} | {check['status']} | {check['detail']} |")

    lines.extend([
        "",
        "Network/source checks:",
        "",
        "| Check | Status | Duration | Notes |",
        "|---|---|---:|---|",
    ])
    for result in report["results"]:
        note = result.get("error", "")
        lines.append(
            f"| {result['name']} | {result['status']} | {result['duration_sec']}s | {note[:120]} |"
        )

    lines.extend([
        "",
        f"Summary: PASS={report['passed']} WARN={report['warned']} FAIL={report['failed']} SKIP={report['skipped']}",
    ])

    hints = report.get("hints") or []
    if hints:
        lines.extend(["", "Hints:"])
        lines.extend([f"- {hint}" for hint in hints])

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CIDector post-install self-check")
    parser.add_argument(
        "--cases",
        default=str(DEFAULT_CASES_FILE),
        help="Path to benchmark cases YAML (default: benchmarks/benchmark_cases.yaml)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-case progress on stderr")
    args = parser.parse_args()

    cases = select_core_cases(Path(args.cases))
    if not cases:
        print(json.dumps({"error": "no self-check cases found"}, ensure_ascii=False))
        sys.exit(1)

    results: list[dict] = []
    for case in cases:
        if args.verbose:
            print(f"[self-check] {case['name']} ...", file=sys.stderr)
        results.append(run_case(case, verbose=args.verbose))

    environment = build_environment_readiness()
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "warned": sum(1 for r in results if r["status"] == "WARN"),
        "failed": sum(1 for r in results if r["status"] == "FAIL"),
        "skipped": sum(1 for r in results if r["status"] == "SKIP"),
        "environment": environment,
        "results": results,
    }
    report["hints"] = build_hints(results)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report), end="")

    critical_env_missing = any(check["status"] == "MISSING" and check["name"] in {"Tavily API key", "Playwright package", "Chromium browser"} for check in environment)
    sys.exit(1 if report["failed"] > 0 or critical_env_missing else 0)


if __name__ == "__main__":
    main()
