#!/usr/bin/env python3
"""Report rendering helpers for CIDector research runs."""

from __future__ import annotations

from datetime import datetime


def extract_claim_candidates(results: list[dict], max_claims: int = 5) -> list[str]:
    """Build simple, reviewable claims from tool outputs for fact-check."""
    claims: list[str] = []
    for result in results:
        data = result.get("data") or {}
        items = data.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if len(claims) >= max_claims:
                return claims
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            metadata = item.get("metadata") or {}
            if metadata.get("nct_id"):
                status = metadata.get("status", "UNKNOWN")
                phase = metadata.get("phase", "")
                phase_part = f" in {phase}" if phase else ""
                claims.append(f"{title} ({metadata['nct_id']}) is {status}{phase_part}")
            elif metadata.get("registration_no"):
                claims.append(
                    f"{title} ({metadata['registration_no']}) is registered in {metadata.get('registry', 'ChinaTrials')}"
                )
            elif item.get("published_at"):
                claims.append(f"{title} was published on {item['published_at']}")
    return claims


def _format_entities(entities: dict) -> list[str]:
    labels = {
        "targets": "靶点",
        "drugs": "药物",
        "companies": "公司",
        "indications": "适应症",
        "modalities": "分子类型",
        "geographic_focus": "地理焦点",
        "time_range": "时间范围",
    }
    lines: list[str] = []
    for key, value in entities.items():
        if value:
            lines.append(f"- {labels.get(key, key)}: {value}")
    if not lines:
        lines.append("- 未提取到高置信实体")
    return lines


def _build_takeaways(execution: dict) -> list[str]:
    results = execution["results"]
    summary = execution["summary"]
    takeaways = [
        f"本轮共执行 {summary['total_steps']} 个数据源步骤，其中 {summary['successful_steps']} 个成功返回有效结果，{summary['errored_steps']} 个失败，{summary['empty_steps']} 个无有效命中。",
    ]

    if summary["fallback_steps"]:
        takeaways.append(f"{summary['fallback_steps']} 个步骤触发了 fallback 或替代来源，引用时需要单独标注来源透明度。")

    top_result = max(results, key=lambda item: item["real_item_count"], default=None)
    if top_result and top_result["real_item_count"] > 0:
        takeaways.append(
            f"结果产出最多的数据源是 {top_result['tool_name']}，返回了 {top_result['real_item_count']} 条可用结果。"
        )

    failing_tools = [r["tool_name"] for r in results if r["status"] == "error"]
    if failing_tools:
        takeaways.append(f"当前仍有未打通的数据源：{', '.join(failing_tools)}。")

    return takeaways[:5]


def _build_execution_table(results: list[dict]) -> list[str]:
    lines = [
        "| 数据源 | 优先级 | 状态 | 可用结果 | 请求来源 | 实际来源 | 备注 |",
        "|---|---|---|---:|---|---|---|",
    ]
    status_map = {"success": "PASS", "empty": "EMPTY", "error": "ERROR"}
    for result in results:
        note = result["error"] or "; ".join(result.get("fallback_reasons") or []) or ("fallback" if result["fallback_used"] else "")
        requested = result.get("requested_source", "")
        actual = ", ".join(result.get("actual_sources") or result.get("source_domains") or [])[:80]
        lines.append(
            f"| {result['tool_name']} | {result['priority']} | {status_map.get(result['status'], result['status'])} | "
            f"{result['real_item_count']} | {requested[:40]} | {actual} | {note[:80]} |"
        )
    return lines


def _build_source_sections(results: list[dict]) -> list[str]:
    lines: list[str] = []
    for result in results:
        lines.append(f"### {result['tool_name']}")
        lines.append(f"- 状态: {result['status']}")
        lines.append(f"- 运行耗时: {result['duration_sec']}s")
        if result.get("requested_source"):
            lines.append(f"- 请求来源: {result['requested_source']}")
        if result.get("actual_sources"):
            lines.append(f"- 实际来源: {', '.join(result['actual_sources'])}")
        elif result.get("source_domains"):
            lines.append(f"- 实际域名: {', '.join(result['source_domains'])}")
        if result["fallback_used"]:
            lines.append("- 说明: 本步骤包含 fallback/替代来源结果")
        if result.get("fallback_reasons"):
            lines.append(f"- fallback 明细: {'; '.join(result['fallback_reasons'])}")
        if result.get("source_mismatch"):
            lines.append("- 提醒: 请求来源与实际命中来源不一致，引用时需单独说明")
        if result["error"]:
            lines.append(f"- 错误: {result['error']}")

        previews = result.get("preview") or []
        if previews:
            for preview in previews:
                line = f"- [{preview.get('title', 'N/A')}]({preview.get('url', '')})"
                if preview.get("published_at"):
                    line += f" | {preview['published_at']}"
                if preview.get("content_preview"):
                    line += f" | {preview['content_preview']}"
                lines.append(line)
        else:
            lines.append("- 无可展示结果")
        lines.append("")
    return lines


def build_report_payload(
    *,
    query: str,
    decision: dict,
    execution: dict,
    fact_check: dict | None = None,
    generated_claims: list[str] | None = None,
) -> dict:
    """Return both structured report payload and markdown rendering."""
    takeaways = _build_takeaways(execution)
    lines = [
        f"# {query}",
        "",
        f"> 研究日期：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 问题类型：{decision['problem_type']}",
        "",
        "## 1. 核心结论",
        "",
    ]
    lines.extend([f"- {item}" for item in takeaways])
    lines.extend([
        "",
        "## 2. 问题分析",
        "",
        f"- 查询：{query}",
        f"- 问题类型：`{decision['problem_type']}`",
        "",
        "### 提取的实体",
    ])
    lines.extend(_format_entities(decision["entities"]))
    lines.extend([
        "",
        "### 推荐分析维度",
    ])
    if decision["analysis_dimensions"]:
        lines.extend([f"- {dim}" for dim in decision["analysis_dimensions"]])
    else:
        lines.append("- 当前问题未命中预设分析维度，将按开放探索处理")

    lines.extend([
        "",
        "## 3. 搜索执行概览",
        "",
    ])
    lines.extend(_build_execution_table(execution["results"]))
    lines.extend([
        "",
        "## 4. 来源要点",
        "",
    ])
    lines.extend(_build_source_sections(execution["results"]))

    lines.extend([
        "## 5. 数据来源透明度",
        "",
        f"- 成功步骤：{execution['summary']['successful_steps']}",
        f"- 空结果步骤：{execution['summary']['empty_steps']}",
        f"- 失败步骤：{execution['summary']['errored_steps']}",
        f"- fallback 步骤：{execution['summary']['fallback_steps']}",
        f"- 来源不一致步骤：{execution['summary'].get('source_mismatch_steps', 0)}",
    ])

    fallback_steps = [result for result in execution["results"] if result.get("fallback_used")]
    if fallback_steps:
        lines.extend([
            "",
            "### Fallback 明细",
            "",
        ])
        for result in fallback_steps:
            detail = "; ".join(result.get("fallback_reasons") or ["未提供明细"])
            actual = ", ".join(result.get("actual_sources") or result.get("source_domains") or ["未知"])
            lines.append(
                f"- {result['tool_name']}: 目标 `{result.get('requested_source', '-')}`，实际 `{actual}`，原因：{detail}"
            )

    if generated_claims:
        lines.extend([
            "",
            "### 自动提取的待核查事实",
            "",
        ])
        lines.extend([f"- {claim}" for claim in generated_claims])

    if fact_check:
        summary = fact_check.get("summary") or {}
        lines.extend([
            "",
            "## 6. Fact-Check 摘要",
            "",
            f"- 核查总数：{summary.get('total', 0)}",
            f"- 已验证：{summary.get('verified', 0)}",
            f"- 可能为真：{summary.get('likely_true', 0)}",
            f"- 存在冲突：{summary.get('conflicting', 0)}",
            f"- 未验证：{summary.get('unverified', 0)}",
        ])
        claims = fact_check.get("claims") or []
        if claims:
            lines.append("")
            for claim in claims[:5]:
                lines.append(f"- `{claim['status']}` {claim['statement']}")

    markdown_report = "\n".join(lines).strip() + "\n"
    return {
        "query": query,
        "decision": decision,
        "execution": execution,
        "generated_claims": generated_claims or [],
        "fact_check": fact_check,
        "markdown_report": markdown_report,
    }
