#!/usr/bin/env python3
"""
CIDector Fact-Check Module - 事实核查与多源交叉验证

用法:
    # 对一组关键事实进行核查
    python skills/research/fact_check.py --facts "Farxiga 2024 销售额 77 亿美元" "Tagrisso 2024 销售额 65.8 亿美元"

    # 从 JSON 文件读取事实列表
    python skills/research/fact_check.py --facts-file claims.json

    # 生成核查报告
    python skills/research/fact_check.py --facts "..." --output report.md

核查流程:
    1. 解析事实陈述 -> 2. 提取验证关键词 -> 3. 多源搜索验证 -> 4. 一致性评估 -> 5. 生成报告
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Literal


# =============================================================================
# 事实核查结果定义
# =============================================================================

VerificationStatus = Literal[
    "verified",        # 已验证 (2+ 独立来源确认)
    "likely_true",     # 可能为真 (1 个可靠来源)
    "conflicting",     # 存在冲突 (不同来源数据不一致)
    "unverified",      # 未验证 (无搜索结果)
    "uncertain",       # 不确定 (信息来源模糊)
]


@dataclass
class FactClaim:
    """事实声明"""
    original_statement: str
    category: str  # "sales_figure", "trial_status", "patent_date", "regulatory", "other"
    key_entities: dict
    verification_status: VerificationStatus = "unverified"
    sources: list[dict] = field(default_factory=list)
    conflicting_info: list[str] = field(default_factory=list)
    notes: str = ""


# =============================================================================
# 事实分类器
# =============================================================================

class ClaimClassifier:
    """事实声明分类器"""

    CATEGORIES = {
        "sales_figure": [
            r"(销售额 | 收入 | 营收|revenue|sales).*(\d+\.?\d*|\d+)\s*(亿 | 百万|B|M)",
            r"\$\d+\.?\d*\s*billion",
            r"贡献.*\d+.*亿",
        ],
        "trial_status": [
            r"(Phase\s*[1-4]|Ⅰ|Ⅱ|Ⅲ|Ⅳ|一期 | 二期 | 三期 | 四期).*(阳性 | 成功 | 达到 | 失败 | 终止)",
            r"(招募 | 入组|recruiting|enrollment).*(完成 | 进行中 | 暂停)",
            r"NCT\d+",
        ],
        "patent_date": [
            r"专利.*(到期 | 截止|expiration|expiry).*(20\d{2})",
            r" exclusivity.* (20\d{2})",
        ],
        "regulatory": [
            r"(FDA|NMPA|EMA|CDE).*(批准 | 获批 | 上市 | 审评 | 审批|approved)",
            r"(PDUFA|优先审评 | 突破性疗法).*(20\d{2})",
        ],
        "clinical_data": [
            r"(ORR|PFS|OS|DCR|DoR).*(\d+\.?\d*|\d+)\s*(%|个月 | 个月)",
            r"(缓解率 | 生存率 | 无进展).*(\d+\.?\d*|\d+)",
        ],
        "timeline": [
            r"(预计 | 计划 | 预期|expected|anticipated).*(20\d{2}).*(H[12]|Q[1-4]|年|月)",
            r"(读出 | readout|公布 | 发布).*(20\d{2})",
        ],
    }

    @classmethod
    def classify(cls, statement: str) -> str:
        """分类事实声明"""
        statement_lower = statement.lower()

        for category, patterns in cls.CATEGORIES.items():
            for pattern in patterns:
                if re.search(pattern, statement_lower, re.IGNORECASE):
                    return category

        return "other"

    @classmethod
    def extract_entities(cls, statement: str) -> dict:
        """从声明中提取关键实体"""
        entities = {
            "drugs": [],
            "companies": [],
            "targets": [],
            "figures": [],
            "dates": [],
            "trial_ids": [],
        }

        # 药物名提取
        drug_patterns = [
            r"\b(Farxiga|Tagrisso|Imfinzi|Enhertu|Datroway|Lynparza|Calquence|Soliris|Tezspire|tozorakimab|baxdrostat|camizestrant)\b",
            r"\b(达伯舒 | 信迪利 | 卡瑞利珠)\b",
        ]
        for pattern in drug_patterns:
            matches = re.findall(pattern, statement, re.IGNORECASE)
            if matches:
                entities["drugs"] = list(set(matches))

        # 公司名提取
        company_patterns = [
            r"\b(AstraZeneca|AZ|阿斯利康 | 恒瑞 | 百利天恒 | 科伦 | 信达 | 君实)\b",
        ]
        for pattern in company_patterns:
            matches = re.findall(pattern, statement, re.IGNORECASE)
            if matches:
                entities["companies"] = list(set(matches))

        # 数字提取
        figure_pattern = r"(\d+\.?\d*)\s*(亿 | 百万|B|M|%|个月|months)"
        matches = re.findall(figure_pattern, statement, re.IGNORECASE)
        if matches:
            entities["figures"] = list(set([f"{m[0]}{m[1]}" for m in matches]))

        # 日期提取
        date_pattern = r"(20\d{2})\s*(年|H[12]|Q[1-4]|月|year)"
        matches = re.findall(date_pattern, statement, re.IGNORECASE)
        if matches:
            entities["dates"] = list(set([f"{m[0]}{m[1]}" for m in matches]))

        # 临床试验 ID 提取
        trial_pattern = r"(NCT\d+|CTR\d+)"
        matches = re.findall(trial_pattern, statement, re.IGNORECASE)
        if matches:
            entities["trial_ids"] = list(set(matches))

        return entities


# =============================================================================
# 验证引擎
# =============================================================================

class VerificationEngine:
    """事实验证引擎"""

    def __init__(self):
        self.results = []

    def verify_claim(self, claim: FactClaim) -> FactClaim:
        """验证单个事实声明"""
        # 生成验证查询
        search_queries = self._generate_search_queries(claim)

        all_sources = []
        for query in search_queries:
            sources = self._search_web(query)
            all_sources.extend(sources)

        # 去重
        unique_sources = self._deduplicate_sources(all_sources)
        claim.sources = unique_sources

        # 评估一致性
        status, conflicts = self._assess_consistency(claim, unique_sources)
        claim.verification_status = status
        claim.conflicting_info = conflicts

        return claim

    def _generate_search_queries(self, claim: FactClaim) -> list[str]:
        """生成验证查询"""
        queries = []
        entities = claim.key_entities

        # 基于类别生成查询
        if claim.category == "sales_figure":
            # 销售额验证：公司 + 药物 + 年份 + 销售额
            if entities["drugs"]:
                drug = entities["drugs"][0]
                dates = entities["dates"] if entities["dates"] else ["2024", "2025"]
                for date in dates:
                    queries.append(f"{drug} {date} sales revenue")
                    queries.append(f"{drug} {date} 销售额")

        elif claim.category == "trial_status":
            # 试验状态验证：药物 + 试验名称 + 结果
            if entities["drugs"]:
                drug = entities["drugs"][0]
                queries.append(f"{drug} Phase 3 trial results")
                if entities["trial_ids"]:
                    queries.append(f"{entities['trial_ids'][0]} results")

        elif claim.category == "patent_date":
            # 专利到期验证：药物 + patent expiration
            if entities["drugs"]:
                drug = entities["drugs"][0]
                queries.append(f"{drug} patent expiration date")

        elif claim.category == "regulatory":
            # 监管审批验证：药物 + FDA/NMPA + approval
            if entities["drugs"]:
                drug = entities["drugs"][0]
                queries.append(f"{drug} FDA approval")
                queries.append(f"{drug} NMPA approval")

        elif claim.category == "clinical_data":
            # 临床数据验证：药物 + ORR/PFS/OS
            if entities["drugs"]:
                drug = entities["drugs"][0]
                queries.append(f"{drug} clinical data ORR PFS OS")

        elif claim.category == "timeline":
            # 时间线验证：药物/公司 + readout/catalyst
            if entities["drugs"]:
                drug = entities["drugs"][0]
                queries.append(f"{drug} 2026 readout catalyst timeline")
            if entities["companies"]:
                company = entities["companies"][0]
                queries.append(f"{company} 2026 pipeline catalyst")

        # 如果没有特定查询，使用原始声明
        if not queries:
            queries.append(claim.original_statement)

        return queries

    def _search_web(self, query: str) -> list[dict]:
        """执行网页搜索"""
        try:
            cmd = [
                "python3", "tools/web_search.py",
                "--query", query,
                "--max-results", "5"
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                items = data.get("items", [])
                sources = []
                for item in items:
                    sources.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", ""),
                        "query": query,
                        "published_at": item.get("published_at", ""),
                    })
                return sources
            else:
                return []

        except Exception as e:
            print(f"搜索失败：{query} - {e}", file=sys.stderr)
            return []

    def _deduplicate_sources(self, sources: list[dict]) -> list[dict]:
        """去重来源"""
        seen_urls = set()
        unique = []
        for source in sources:
            url = source.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(source)
        return unique

    def _assess_consistency(
        self, claim: FactClaim, sources: list[dict]
    ) -> tuple[VerificationStatus, list[str]]:
        """评估来源一致性"""
        if not sources:
            return "unverified", []

        # 检查是否有多个独立来源
        independent_sources = self._count_independent_sources(sources)
        conflicts = self._detect_conflicts(sources, claim)

        if conflicts:
            return "conflicting", conflicts

        if independent_sources >= 2:
            # 检查来源可靠性
            reliable_count = self._count_reliable_sources(sources)
            if reliable_count >= 2:
                return "verified", []
            elif reliable_count >= 1:
                return "likely_true", []

        if independent_sources >= 1:
            return "likely_true", []

        return "unverified", []

    def _count_independent_sources(self, sources: list[dict]) -> int:
        """计算独立来源数量"""
        # 按域名分组
        domain_groups = {}
        for source in sources:
            url = source.get("url", "")
            domain = self._extract_domain(url)
            if domain:
                if domain not in domain_groups:
                    domain_groups[domain] = []
                domain_groups[domain].append(source)

        # 合并同一域名的来源
        return len(domain_groups)

    def _extract_domain(self, url: str) -> str:
        """提取域名"""
        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if match:
            return match.group(1)
        return ""

    def _count_reliable_sources(self, sources: list[dict]) -> int:
        """计算可靠来源数量"""
        reliable_domains = [
            "astrazeneca.com",
            "reuters.com",
            "fiercepharma.com",
            "endpts.com",
            "biocentury.com",
            "pharmalive.com",
            "seekingalpha.com",
            "cnbc.com",
            "bloomberg.com",
            "caixinglobal.com",
        ]

        count = 0
        seen_domains = set()
        for source in sources:
            url = source.get("url", "")
            domain = self._extract_domain(url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                # 检查是否是可靠来源
                for reliable in reliable_domains:
                    if reliable in domain:
                        count += 1
                        break
                # 官方 PDF 也算可靠
                if url.endswith(".pdf"):
                    count += 1

        return count

    def _detect_conflicts(
        self, sources: list[dict], claim: FactClaim
    ) -> list[str]:
        """检测来源冲突"""
        conflicts = []
        key_figures = claim.key_entities.get("figures", [])

        for source in sources:
            content = source.get("content", "").lower()
            title = source.get("title", "").lower()
            text = content + " " + title

            # 检查是否有不同数字
            for figure in key_figures:
                if figure not in text:
                    # 提取来源中的数字
                    source_figures = re.findall(
                        r"(\d+\.?\d*)\s*(亿 | 百万|B|M|%|billion|million)",
                        text,
                        re.IGNORECASE
                    )
                    for sf in source_figures:
                        sf_normalized = f"{sf[0]}{sf[1]}".lower()
                        figure_normalized = figure.lower()
                        # 检查是否是不同数量级
                        if sf[1].lower() != figure.split()[0][-1:] if len(figure) > 0 else False:
                            if sf_normalized != figure_normalized:
                                conflict = f"来源 {source.get('url', '')}: {sf[0]}{sf[1]} vs 声明的 {figure}"
                                if conflict not in conflicts:
                                    conflicts.append(conflict)

        return conflicts


# =============================================================================
# 报告生成器
# =============================================================================

class ReportGenerator:
    """报告生成器"""

    @staticmethod
    def generate_markdown(
        claims: list[FactClaim], output_file: str = None
    ) -> str:
        """生成 Markdown 格式核查报告"""
        lines = [
            "# 事实核查报告",
            "",
            f"> 生成时间：{__import__('datetime').datetime.now().isoformat()}",
            "",
            f"## 核查摘要",
            "",
            f"- 总核查数量：{len(claims)}",
            f"- 已验证：{sum(1 for c in claims if c.verification_status == 'verified')}",
            f"- 可能为真：{sum(1 for c in claims if c.verification_status == 'likely_true')}",
            f"- 存在冲突：{sum(1 for c in claims if c.verification_status == 'conflicting')}",
            f"- 未验证：{sum(1 for c in claims if c.verification_status == 'unverified')}",
            "",
            "## 详细核查结果",
            "",
        ]

        for i, claim in enumerate(claims, 1):
            status_emoji = {
                "verified": "✅",
                "likely_true": "🟡",
                "conflicting": "⚠️",
                "unverified": "❌",
                "uncertain": "❓",
            }.get(claim.verification_status, "❓")

            lines.extend([
                f"### {i}. {claim.original_statement}",
                "",
                f"- **状态**: {status_emoji} {claim.verification_status}",
                f"- **类别**: {claim.category}",
                f"- **关键实体**: {json.dumps(claim.key_entities, ensure_ascii=False)}",
                "",
            ])

            if claim.sources:
                lines.append("**来源**:")
                for j, source in enumerate(claim.sources[:5], 1):  # 最多显示 5 个
                    lines.append(
                        f"  {j}. [{source.get('title', 'N/A')}]({source.get('url', 'N/A')})"
                    )
                lines.append("")

            if claim.conflicting_info:
                lines.extend([
                    "**⚠️ 冲突信息**:",
                ] + [f"  - {c}" for c in claim.conflicting_info] + [""]
            )

            if claim.notes:
                lines.extend([
                    f"**备注**: {claim.notes}",
                    "",
                ])

        lines.extend([
            "---",
            "",
            "## 数据来源透明度总结",
            "",
            "### 已验证事实（至少 2 个独立来源）",
        ])

        verified = [c for c in claims if c.verification_status == "verified"]
        if verified:
            for claim in verified:
                urls = [s.get("url", "") for s in claim.sources[:3]]
                lines.append(f"- {claim.original_statement}: [URL]({urls[0] if urls else '#'})")
        else:
            lines.append("暂无")

        lines.extend([
            "",
            "### 单一来源事实（需进一步验证）",
        ])

        likely_true = [c for c in claims if c.verification_status == "likely_true"]
        if likely_true:
            for claim in likely_true:
                urls = [s.get("url", "") for s in claim.sources[:1]]
                lines.append(f"- {claim.original_statement}: [URL]({urls[0] if urls else '#'})")
        else:
            lines.append("暂无")

        lines.extend([
            "",
            "### 不确定/冲突信息",
        ])

        conflicting = [c for c in claims if c.verification_status in ["conflicting", "unverified"]]
        if conflicting:
            for claim in conflicting:
                lines.append(f"- {claim.original_statement}: {claim.conflicting_info or '无可靠来源'}")
        else:
            lines.append("暂无")

        report = "\n".join(lines)

        if output_file:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"报告已保存至：{output_file}")

        return report


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="CIDector Fact-Check Module - 事实核查与多源交叉验证"
    )
    parser.add_argument(
        "--facts", "-f",
        nargs="+",
        help="要验证的事实声明列表"
    )
    parser.add_argument(
        "--facts-file",
        help="包含事实声明的 JSON 文件"
    )
    parser.add_argument(
        "--output", "-o",
        help="输出报告文件路径"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="markdown",
        help="输出格式"
    )

    args = parser.parse_args()

    # 获取事实列表
    claims_input = []
    if args.facts:
        claims_input = args.facts
    elif args.facts_file:
        with open(args.facts_file, "r", encoding="utf-8") as f:
            claims_input = json.load(f).get("claims", [])
    else:
        parser.print_help()
        sys.exit(1)

    # 分类和验证
    engine = VerificationEngine()
    claims = []

    print("开始事实核查...")
    print("=" * 60)

    for statement in claims_input:
        print(f"\n核查：{statement}")

        category = ClaimClassifier.classify(statement)
        entities = ClaimClassifier.extract_entities(statement)

        claim = FactClaim(
            original_statement=statement,
            category=category,
            key_entities=entities,
        )

        print(f"  类别：{category}")
        print(f"  实体：{entities}")

        verified_claim = engine.verify_claim(claim)
        claims.append(verified_claim)

        print(f"  状态：{verified_claim.verification_status}")
        print(f"  来源数：{len(verified_claim.sources)}")

    print("\n" + "=" * 60)

    # 生成报告
    if args.format == "json":
        output = json.dumps([
            {
                "statement": c.original_statement,
                "category": c.category,
                "status": c.verification_status,
                "sources": c.sources,
                "conflicts": c.conflicting_info,
            }
            for c in claims
        ], ensure_ascii=False, indent=2)
        print(output)
    else:
        report = ReportGenerator.generate_markdown(claims, args.output)
        if not args.output:
            print(report)


if __name__ == "__main__":
    main()
