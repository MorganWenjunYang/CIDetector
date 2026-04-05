"""GitHub Issue body/title generation with error classification and fix guidance.

Transforms raw benchmark failure data into actionable issues that give the
fixing agent maximum context to diagnose and resolve the problem efficiently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

@dataclass
class ErrorClassification:
    category: str          # e.g. "http_403", "empty_results", "crash"
    root_cause_hint: str   # one-sentence hypothesis
    suggested_approach: str  # what the fixing agent should try
    affected_layer: str    # "network", "parsing", "logic", "dependency"


_TOOL_TO_SOURCE: dict[str, dict[str, Any]] = {
    "tools/search_clinical_trials.py": {
        "source": "ClinicalTrials.gov REST API (v2)",
        "deps": ["utils/http_client.py::fetch_json"],
        "api_docs": "https://clinicaltrials.gov/data-api/api",
    },
    "tools/search_pubmed.py": {
        "source": "NCBI E-Utilities (PubMed)",
        "deps": ["utils/http_client.py::fetch_json"],
        "api_docs": "https://www.ncbi.nlm.nih.gov/books/NBK25501/",
    },
    "tools/web_search.py": {
        "source": "Tavily Search API",
        "deps": ["utils/http_client.py::fetch_json"],
        "api_docs": "https://docs.tavily.com",
    },
    "tools/search_china_trials.py": {
        "source": "CDE / ChiCTR HTML scraping",
        "deps": [
            "utils/http_client.py::fetch_text",
            "utils/http_client.py::fetch_text_auto",
            "utils/parsers.py",
        ],
        "api_docs": None,
        "note": "HTML scraping — fragile to site redesign and anti-bot measures",
    },
    "tools/search_stock_disclosure.py": {
        "source": "SSE/HKEX disclosure search",
        "deps": ["utils/http_client.py::fetch_json", "utils/parsers.py"],
        "api_docs": None,
    },
    "tools/search_conferences.py": {
        "source": "ASCO/AACR/ESMO abstract search (HTML scraping)",
        "deps": [
            "utils/http_client.py::fetch_text",
            "utils/http_client.py::fetch_text_auto",
            "utils/parsers.py",
        ],
        "api_docs": None,
        "note": "HTML scraping — fragile to site redesign and anti-bot measures",
    },
    "tools/rss_monitor.py": {
        "source": "RSS feed parsing",
        "deps": ["utils/http_client.py::fetch_text"],
        "api_docs": None,
    },
    "tools/fetch_page.py": {
        "source": "Generic page fetcher (httpx + Playwright fallback)",
        "deps": [
            "utils/http_client.py::fetch_text_auto",
            "utils/http_client.py::fetch_text_browser",
        ],
        "api_docs": None,
    },
}


def _classify_error(error_text: str, tool_path: str) -> ErrorClassification:
    """Classify a benchmark error into an actionable category."""
    err = error_text.lower()

    if "403" in err or "forbidden" in err:
        return ErrorClassification(
            category="http_403_forbidden",
            root_cause_hint=(
                "目标网站返回 403 Forbidden，通常因为反爬策略升级、"
                "User-Agent 被拦截、或需要 cookie/session 认证。"
            ),
            suggested_approach=(
                "1) 检查 utils/http_client.py 中的 User-Agent 和 headers 是否过时\n"
                "2) 判断是否需要切换到 Playwright 浏览器模式 (fetch_text_browser)\n"
                "3) 检查目标网站是否有新的反爬机制 (Cloudflare, WAF 等)\n"
                "4) 如果网站永久封锁，考虑使用替代数据源或 API"
            ),
            affected_layer="network",
        )

    if "timeout" in err or "timed out" in err:
        return ErrorClassification(
            category="timeout",
            root_cause_hint="请求超时，可能是网络问题、目标服务慢、或超时时间设置不足。",
            suggested_approach=(
                "1) 增加 timeout 参数值\n"
                "2) 检查目标 URL 是否可达\n"
                "3) 如果是 Playwright 模式，增加 wait_until 超时"
            ),
            affected_layer="network",
        )

    if "antibot" in err or "challenge" in err or "acw_sc" in err:
        return ErrorClassification(
            category="antibot_challenge",
            root_cause_hint="检测到反爬验证页面（JS challenge），普通 HTTP 请求无法获取真实内容。",
            suggested_approach=(
                "1) 确认 fetch_text_auto 的反爬检测逻辑是否正确触发 Playwright 回退\n"
                "2) 检查 _looks_like_antibot() 是否覆盖了新的反爬签名\n"
                "3) Playwright 浏览器模式是否正常工作（检查 playwright install）"
            ),
            affected_layer="network",
        )

    if "0 real items" in err or "empty" in err.lower():
        return ErrorClassification(
            category="empty_results",
            root_cause_hint=(
                "工具执行成功但返回 0 条有效结果。可能原因：HTML 结构变更导致解析失败、"
                "查询条件过严、或上游数据源暂无数据。"
            ),
            suggested_approach=(
                "1) 手动运行复现命令，查看原始输出\n"
                "2) 用 fetch_page.py 抓取目标页面，对比 HTML 结构与解析逻辑\n"
                "3) 检查 CSS selector / XPath / 正则是否匹配当前页面结构\n"
                "4) 区分「网站无数据」和「解析失败」"
            ),
            affected_layer="parsing",
        )

    if "json" in err and ("decode" in err or "invalid" in err):
        return ErrorClassification(
            category="json_parse_error",
            root_cause_hint="工具输出不是有效 JSON，可能是工具内部 print 了非 JSON 内容、或 API 返回了非 JSON 响应。",
            suggested_approach=(
                "1) 检查工具是否有 debug print 写到了 stdout（应该写 stderr）\n"
                "2) 检查 API 响应是否变为 HTML 错误页\n"
                "3) 确保 json.dumps 的输出是工具唯一的 stdout 输出"
            ),
            affected_layer="parsing",
        )

    if "exit code" in err:
        exit_match = re.search(r"exit code (\d+)", err)
        code = exit_match.group(1) if exit_match else "non-zero"
        traceback_present = "traceback" in err
        return ErrorClassification(
            category="crash",
            root_cause_hint=f"工具以 exit code {code} 崩溃退出。{'有 Traceback 信息可用于定位。' if traceback_present else ''}",
            suggested_approach=(
                "1) 阅读 Traceback 定位崩溃位置\n"
                "2) 检查依赖是否缺失（import error）\n"
                "3) 检查环境变量是否缺失\n"
                "4) 本地运行复现命令确认"
            ),
            affected_layer="dependency" if "import" in err else "logic",
        )

    if "missing" in err:
        return ErrorClassification(
            category="missing_field",
            root_cause_hint="输出 JSON 缺少必需字段，可能是 API 响应格式变更或代码未正确构建输出。",
            suggested_approach=(
                "1) 检查工具输出的 JSON 结构\n"
                "2) 对比 API 实际返回与代码中的字段映射\n"
                "3) 确保 source/items 等字段始终存在"
            ),
            affected_layer="logic",
        )

    return ErrorClassification(
        category="unknown",
        root_cause_hint="无法自动分类此错误，需要人工分析。",
        suggested_approach=(
            "1) 手动运行复现命令查看完整输出\n"
            "2) 阅读工具源码理解完整流程\n"
            "3) 在关键路径添加 debug 日志辅助诊断"
        ),
        affected_layer="unknown",
    )


def _get_tool_info(tool_path: str) -> dict[str, Any]:
    """Get metadata about a tool for the issue body."""
    return _TOOL_TO_SOURCE.get(tool_path, {})


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

def build_issue_title(case: dict, date_str: str) -> str:
    """Build a concise, searchable issue title."""
    status_tag = case["status"]  # FAIL or WARN
    name = case["name"]
    error_text = case.get("error", "")

    classification = _classify_error(error_text, "")
    category_short = classification.category.replace("_", " ").upper()

    return f"[Benchmark][{status_tag}] {name} — {category_short} ({date_str})"


# ---------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------

def build_issue_body(
    case: dict,
    report: dict,
    benchmark_cases: list[dict],
) -> str:
    """Build a structured, actionable issue body for a benchmark failure.

    Designed to give a fixing agent maximum context to resolve the issue
    without needing to explore the codebase from scratch.
    """
    error_text = case.get("error", "unknown error")
    tool_cmd = ""
    tool_path = ""
    for c in benchmark_cases:
        if c["name"] == case["name"]:
            tool_path = c["tool"]
            tool_cmd = f"python {c['tool']} {' '.join(c.get('args', []))}"
            break

    classification = _classify_error(error_text, tool_path)
    tool_info = _get_tool_info(tool_path)

    lines: list[str] = []

    # -- TL;DR for quick triage --
    lines.append("## TL;DR")
    lines.append("")
    lines.append(
        f"**{case['name']}** benchmark 失败 ({case['status']})。"
        f"错误分类: **{classification.category}** ({classification.affected_layer} 层)。"
    )
    lines.append(f"根因假设: {classification.root_cause_hint}")
    lines.append("")

    # -- Error details --
    lines.append("## 错误详情")
    lines.append("")
    lines.append(f"- **Status**: {case['status']}")
    lines.append(f"- **Duration**: {case.get('duration_sec', '?')}s")
    lines.append(f"- **Error category**: `{classification.category}`")
    lines.append(f"- **Affected layer**: {classification.affected_layer}")
    lines.append("")
    lines.append("```")
    lines.append(error_text)
    lines.append("```")
    lines.append("")

    # -- Reproduce --
    if tool_cmd:
        lines.append("## 复现步骤")
        lines.append("")
        lines.append("```bash")
        lines.append(f"cd {'{project_root}'}  # 项目根目录")
        lines.append(tool_cmd)
        lines.append("```")
        lines.append("")

    # -- Source code map --
    lines.append("## 相关代码路径")
    lines.append("")
    if tool_path:
        lines.append(f"- **主工具文件**: `{tool_path}`")
    if tool_info.get("deps"):
        lines.append("- **依赖链**:")
        for dep in tool_info["deps"]:
            lines.append(f"  - `{dep}`")
    if tool_info.get("source"):
        lines.append(f"- **数据源**: {tool_info['source']}")
    if tool_info.get("api_docs"):
        lines.append(f"- **API 文档**: {tool_info['api_docs']}")
    if tool_info.get("note"):
        lines.append(f"- **注意**: {tool_info['note']}")
    lines.append("- **Benchmark 配置**: `benchmarks/benchmark_cases.yaml`")
    lines.append("- **共享 HTTP 客户端**: `utils/http_client.py`")
    lines.append("")

    # -- Suggested fix approach --
    lines.append("## 建议修复方向")
    lines.append("")
    for line in classification.suggested_approach.split("\n"):
        lines.append(line)
    lines.append("")

    # -- Expected vs Actual --
    lines.append("## 预期行为")
    lines.append("")
    lines.append(
        f"- `{case['name']}` 应返回 **至少 1 条有效结果** 且 exit code 为 0"
    )
    lines.append(
        "- 输出必须是符合 `{source, query, total_results, items}` 规范的有效 JSON"
    )
    lines.append("")

    # -- Benchmark context --
    lines.append("## 本次 Benchmark 概览")
    lines.append("")
    passed = report.get("passed", 0)
    failed = report.get("failed", 0)
    warned = report.get("warned", 0)
    skipped = report.get("skipped", 0)
    lines.append("| Passed | Failed | Warned | Skipped |")
    lines.append("|--------|--------|--------|---------|")
    lines.append(f"| {passed} | {failed} | {warned} | {skipped} |")
    lines.append("")

    # -- Other failing cases (if any) --
    other_failures = [
        r for r in report.get("results", [])
        if r["status"] in ("FAIL", "WARN") and r["name"] != case["name"]
    ]
    if other_failures:
        lines.append("### 其他失败项 (可能相关)")
        lines.append("")
        for r in other_failures:
            lines.append(f"- **{r['name']}** ({r['status']}): {r.get('error', '')[:120]}")
        lines.append("")

    # -- Metadata --
    lines.append("---")
    lines.append(
        f"*Auto-generated by `orchestrate/orchestrator.py benchmark` — "
        f"{report.get('timestamp', 'N/A')[:10]}*"
    )

    return "\n".join(lines)
