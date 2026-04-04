#!/usr/bin/env python3
"""
CIDector Research Skill - 生物医药竞争情报研究决策引擎

用法:
    python skills/research.py --query "B7H4 ADC 竞品分析"
    python skills/research.py --query "百利天恒 BD 交易" --mode plan
    python skills/research.py --query "pembrolizumab 临床数据" --auto-execute

决策框架:
    1. 问题分析 -> 2. 实体提取 -> 3. 分析维度设计 -> 4. 数据源选择 -> 5. 生成计划
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Literal


# =============================================================================
# 问题类型定义
# =============================================================================

ProblemType = Literal[
    "pipeline_deep_dive",        # 深度管线分析 (靶点/适应症级别)
    "asset_comparison",          # 资产对比分析
    "clinical_trial_progress",   # 临床试验进展
    "clinical_data_efficacy",    # 临床数据/疗效
    "industry_news_bd",          # 行业新闻/BD 交易
    "china_vs_global",           # 中国 vs 全球对比
    "regulatory_approval",       # 监管审批
    "conference_data",           # 会议数据
    "capital_market",            # 资本市场
    "company_profile",           # 公司全景分析
    "failure_analysis",          # 失败项目分析
    "open_exploration",          # 开放探索
]


# =============================================================================
# 决策知识库
# =============================================================================

@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    script: str
    description: str
    default_params: dict = field(default_factory=dict)


# 工具注册表
TOOLS = {
    "clinical_trials": ToolDefinition(
        name="临床试验搜索",
        script="tools/search_clinical_trials.py",
        description="ClinicalTrials.gov 试验搜索",
        default_params={"--max-results": "20"}
    ),
    "pubmed": ToolDefinition(
        name="PubMed 文献",
        script="tools/search_pubmed.py",
        description="学术文献搜索",
        default_params={"--max-results": "10"}
    ),
    "web_search": ToolDefinition(
        name="网页搜索",
        script="tools/web_search.py",
        description="Tavily 网页搜索",
        default_params={"--max-results": "10"}
    ),
    "china_trials": ToolDefinition(
        name="中国临床试验",
        script="tools/search_china_trials.py",
        description="中国 CDE/临床试验注册",
        default_params={"--max-results": "20"}
    ),
    "stock_disclosure": ToolDefinition(
        name="资本市场公告",
        script="tools/search_stock_disclosure.py",
        description="上市公司公告/财报/监管文件",
        default_params={"--max-results": "10"}
    ),
    "conferences": ToolDefinition(
        name="学术会议",
        script="tools/search_conferences.py",
        description="AACR/ASCO/ESMO 会议摘要",
        default_params={"--max-results": "10"}
    ),
    "fetch_page": ToolDefinition(
        name="网页抓取",
        script="tools/fetch_page.py",
        description="抓取网页全文",
        default_params={}
    ),
}

# 问题类型 -> 工具映射
PROBLEM_TYPE_TOOLS = {
    "pipeline_deep_dive": {
        "primary": ["clinical_trials", "web_search"],
        "secondary": ["pubmed", "conferences"]
    },
    "asset_comparison": {
        "primary": ["clinical_trials", "pubmed"],
        "secondary": ["web_search", "conferences"]
    },
    "clinical_trial_progress": {
        "primary": ["clinical_trials"],
        "secondary": ["pubmed", "web_search"]
    },
    "clinical_data_efficacy": {
        "primary": ["pubmed"],
        "secondary": ["conferences", "web_search"]
    },
    "industry_news_bd": {
        "primary": ["web_search"],
        "secondary": ["stock_disclosure"]
    },
    "china_vs_global": {
        "primary": ["clinical_trials", "china_trials", "web_search"],
        "secondary": ["conferences", "stock_disclosure"]
    },
    "regulatory_approval": {
        "primary": ["web_search"],
        "secondary": ["china_trials"]
    },
    "conference_data": {
        "primary": ["conferences"],
        "secondary": ["pubmed"]
    },
    "capital_market": {
        "primary": ["stock_disclosure"],
        "secondary": ["web_search"]
    },
    "company_profile": {
        "primary": ["web_search"],
        "secondary": ["clinical_trials", "stock_disclosure"]
    },
    "failure_analysis": {
        "primary": ["web_search", "clinical_trials"],
        "secondary": ["pubmed"]
    },
    "open_exploration": {
        "primary": ["web_search"],
        "secondary": []
    },
}

# 关键词 -> 问题类型映射
KEYWORD_PATTERNS = {
    "pipeline_deep_dive": [
        r"(靶点|靶点).* (管线|格局|全景)",
        r"靶点.* (ADC|CAR-T|双抗|小分子)",
        r"pipeline.*deep.*dive",
        r"competitive.*landscape",
        r"管线全景",
        r"竞争格局",
    ],
    "asset_comparison": [
        r"(资产|药物|产品).*对比",
        r"头对头",
        r"benchmark",
        r"vs\.|versus",
        r"差异化",
        r"基准资产",
        r"head-to-head",
    ],
    "clinical_trial_progress": [
        r"临床 (试验|实验|研究).*(进展|状态|招募)",
        r"临床.*(一期|二期|三期|四期|I期|II期|III期|IV期)",
        r"trial.*progress",
        r"recruiting",
        r"NCT\d+",
    ],
    "clinical_data_efficacy": [
        r"(临床|试验|研究).*(数据|疗效|结果|安全性)",
        r"(ORR|PFS|OS|DCR|DoR|PGR).*(数据|结果|率)",
        r"efficacy",
        r"clinical.*data",
        r"缓解率 | 生存率 | 无进展",
    ],
    "industry_news_bd": [
        r"(BD|授权|许可|交易|合作|引进|出售|收购)",
        r"licens",
        r"partnership",
        r"acquisition",
        r"战略合作",
        r"首付款 | 里程碑",
    ],
    "china_vs_global": [
        r"(中国|国内).*(vs|对比|差异).* (全球|海外|美国)",
        r"China.*vs.*Global",
        r"出海 | 国际化",
        r"中美双报",
        r"fast.*follow",
    ],
    "regulatory_approval": [
        r"(批准|获批|上市|审批|审评)",
        r"approv",
        r"FDA|NMPA|EMA|CDE",
        r"突破性疗法 | 优先审评",
    ],
    "conference_data": [
        r"(AACR|ASCO|ESMO|ASH|SABCS|WCLC|CSCO)",
        r"(会议|大会|海报|口头报告)",
        r"conference",
        r"abstract",
    ],
    "capital_market": [
        r"(财报|公告|招股|季报|年报)",
        r"earnings",
        r"IPO",
        r"10-K|10-Q",
    ],
    "failure_analysis": [
        r"(失败|终止|暂停|撤回|未达)",
        r"fail|terminated|discontinued",
        r"安全性.*问题",
        r"疗效.*不足",
    ],
    "company_profile": [
        r"(公司|企业).*(概况|全景|介绍|战略)",
        r"company.*profile",
        r"pipeline.*company",
    ],
}

# =============================================================================
# 分析维度模板 (来自专业 prompt 的启示)
# =============================================================================

ANALYSIS_DIMENSIONS = {
    "pipeline_deep_dive": [
        "分子设计与差异化 (Payload/Linker/双抗架构)",
        "临床定位 (适应症选择/线数定位)",
        "安全性特征 (治疗窗口/毒性谱)",
        "监管策略 (突破性疗法/快速通道)",
        "中国 vs 全球策略差异",
    ],
    "asset_comparison": [
        "头对头数据对比 (ORR/PFS/OS)",
        "安全性对比 (AE 谱/治疗窗口)",
        "给药方案便利性",
        "监管路径与时间线",
        "商业化潜力",
    ],
    "china_vs_global": [
        "中国首创 vs 全球首创路径",
        "适应症选择差异 (如 NPC 等中国特色)",
        "监管时间窗差异",
        "临床资源可及性",
        "出海机会与壁垒",
    ],
    "failure_analysis": [
        "失败原因分类 (安全性/疗效/CMC/商业)",
        "分子设计教训",
        "患者选择问题",
        "对同类项目的启示",
        "目标边界重新定义",
    ],
}


# =============================================================================
# 决策引擎
# =============================================================================

class ResearchDecisionEngine:
    """研究决策引擎"""

    def __init__(self, query: str):
        self.query = query
        self.problem_type: ProblemType = self._classify_problem()
        self.entities = self._extract_entities()
        self.analysis_dimensions = self._get_analysis_dimensions()
        self.search_plan = self._generate_search_plan()

    def _classify_problem(self) -> ProblemType:
        """问题分析 - 识别问题类型"""
        query_lower = self.query.lower()

        for ptype, patterns in KEYWORD_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query_lower, re.IGNORECASE):
                    return ptype  # type: ignore

        return "open_exploration"

    def _extract_entities(self) -> dict:
        """实体提取 - 从查询中提取关键实体"""
        entities = {
            "targets": [],
            "drugs": [],
            "companies": [],
            "indications": [],
            "modalities": [],
            "time_range": None,
            "geographic_focus": None,
        }

        # 靶点提取 (常见格式)
        target_patterns = [
            r"\b(B7H[0-4]|B7-H[0-4]|CLDN[0-9]+|CLD[0-9]+|HER[0-3]|EGFR|PD-?1|PD-L1|TROP2|Nectin-?4)\b",
            r"\b(VTCN1|CD276|CDH3|ERBB[0-3])\b",  # 别名
        ]
        for pattern in target_patterns:
            matches = re.findall(pattern, self.query, re.IGNORECASE)
            if matches:
                entities["targets"] = list(set(matches))

        # 药物名提取 (通常包含字母 + 数字)
        drug_pattern = r"\b([A-Z][a-z]+[a-z0-9]+(?:mab|toxin|tecan|deruxtecan|cept))\b"
        matches = re.findall(drug_pattern, self.query, re.IGNORECASE)
        if matches:
            entities["drugs"] = list(set(matches))

        # 公司名提取 (中英文)
        company_patterns = [
            r"\b(阿斯利康|恒瑞|百利天恒|科伦|荣昌|信达|君实)\b",
            r"\b(AstraZeneca|Pfizer|Merck|BMS|Gilead)\b",
        ]
        for pattern in company_patterns:
            matches = re.findall(pattern, self.query)
            if matches:
                entities["companies"] = list(set(matches))

        # 适应症提取
        indication_patterns = [
            r"\b(SCLC|NSCLC|NPC|HCC|TNBC|CRC|AML|MM)\b",
            r"\b(小细胞肺癌|非小细胞肺癌|鼻咽癌|肝癌|乳腺癌|结直肠癌)\b",
        ]
        for pattern in indication_patterns:
            matches = re.findall(pattern, self.query, re.IGNORECASE)
            if matches:
                entities["indications"] = list(set(matches))

        # 分子类型提取
        modality_patterns = [
            r"\b(ADC|CAR-T|双抗|小分子|抗体|融合蛋白)\b",
        ]
        for pattern in modality_patterns:
            matches = re.findall(pattern, self.query, re.IGNORECASE)
            if matches:
                entities["modalities"] = list(set(matches))

        # 地理焦点
        if re.search(r"(中国|国内|China)", self.query, re.IGNORECASE):
            entities["geographic_focus"] = "China"
        if re.search(r"(全球|海外|美国|Global)", self.query, re.IGNORECASE):
            entities["geographic_focus"] = "Global"

        return entities

    def _get_analysis_dimensions(self) -> list[str]:
        """获取推荐的分析维度"""
        return ANALYSIS_DIMENSIONS.get(self.problem_type, [])

    def _generate_search_plan(self) -> list[dict]:
        """数据源选择 - 生成搜索计划"""
        type_config = PROBLEM_TYPE_TOOLS.get(
            self.problem_type,
            PROBLEM_TYPE_TOOLS["open_exploration"]
        )

        plan = []

        # 添加主要数据源
        for tool_key in type_config["primary"]:
            plan.append({
                "tool": tool_key,
                "priority": "primary",
                "reason": f"问题类型 '{self.problem_type}' 的首选数据源",
                "params": self._build_tool_params(tool_key)
            })

        # 添加补充数据源
        for tool_key in type_config["secondary"]:
            plan.append({
                "tool": tool_key,
                "priority": "secondary",
                "reason": f"问题类型 '{self.problem_type}' 的补充数据源",
                "params": self._build_tool_params(tool_key)
            })

        return plan

    def _build_tool_params(self, tool_key: str) -> dict:
        """为特定工具构建参数"""
        base_params = TOOLS[tool_key].default_params.copy()

        # 根据查询内容添加特定参数
        if tool_key == "web_search":
            site_match = re.search(r"--site\s+(\S+)", self.query)
            if site_match:
                base_params["--site"] = site_match.group(1)
            days_match = re.search(r"--days\s+(\d+)", self.query)
            if days_match:
                base_params["--days"] = days_match.group(1)

        if tool_key == "clinical_trials":
            phase_match = re.search(
r"(Phase\s*[1-4]|Ⅰ|Ⅱ|Ⅲ|Ⅳ|一期|二期|三期|四期)",
                self.query, re.IGNORECASE
            )
            if phase_match:
                base_params["--phase"] = phase_match.group(1)

            status_match = re.search(
r"(RECRUITING|COMPLETED|ACTIVE|TERMINATED|招募|完成)",
                self.query, re.IGNORECASE
            )
            if status_match:
                base_params["--status"] = status_match.group(1).upper()

        return base_params

    def get_plan_summary(self) -> str:
        """生成人类可读的计划摘要"""
        lines = [
            "## 问题分析",
            f"- **问题类型**: `{self.problem_type}`",
            f"- **查询**: {self.query}",
        ]

        # 输出提取的实体
        if any(self.entities.values()):
            lines.append("\n### 提取的实体")
            for key, value in self.entities.items():
                if value:
                    label = {
                        "targets": "靶点",
                        "drugs": "药物",
                        "companies": "公司",
                        "indications": "适应症",
                        "modalities": "分子类型",
                        "geographic_focus": "地理焦点",
                        "time_range": "时间范围",
                    }.get(key, key)
                    lines.append(f"- **{label}**: {value}")

        # 输出推荐分析维度
        if self.analysis_dimensions:
            lines.append("\n### 推荐分析维度")
            for dim in self.analysis_dimensions:
                lines.append(f"- {dim}")

        lines.append("\n## 搜索计划")

        if not self.search_plan:
            lines.append("暂无搜索计划")
            return "\n".join(lines)

        for i, step in enumerate(self.search_plan, 1):
            tool_info = TOOLS.get(step["tool"])
            tool_name = tool_info.name if tool_info else step["tool"]

            lines.append(f"\n### 步骤 {i}: {tool_name}")
            lines.append(f"- **优先级**: {step['priority']}")
            lines.append(f"- **原因**: {step['reason']}")

            params_str = " ".join(
                f"{k} {v}" for k, v in step["params"].items()
            )
            script = TOOLS[step["tool"]].script
            lines.append(f"- **命令**: `python {script} --query \"{self.query}\" {params_str}`")

        return "\n".join(lines)

    def to_json(self) -> dict:
        """输出 JSON 格式计划"""
        return {
            "query": self.query,
            "problem_type": self.problem_type,
            "entities": self.entities,
            "analysis_dimensions": self.analysis_dimensions,
            "search_plan": self.search_plan
        }


# =============================================================================
# CLI 入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="CIDetector Research Skill - 研究决策引擎"
    )
    parser.add_argument(
        "--query", "-q",
        required=True,
        help="研究问题/查询"
    )
    parser.add_argument(
        "--mode",
        choices=["plan", "full"],
        default="plan",
        help="模式：plan=只输出计划，full=输出完整分析框架"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="输出格式"
    )
    parser.add_argument(
        "--output", "-o",
        help="输出文件路径"
    )
    parser.add_argument(
        "--auto-execute",
        action="store_true",
        help="自动执行工具调用 (需要用户确认)"
    )
    parser.add_argument(
        "--fact-check",
        action="store_true",
        help="在分析完成后对关键事实进行交叉验证"
    )
    parser.add_argument(
        "--claims-file",
        help="关键事实声明 JSON 文件 (用于 fact-check)"
    )

    args = parser.parse_args()

    engine = ResearchDecisionEngine(args.query)

    if args.format == "json":
        print(json.dumps(engine.to_json(), ensure_ascii=False, indent=2))
    else:
        print(engine.get_plan_summary())

    if args.auto_execute:
        print("\n" + "=" * 60)
        print("## 执行工具调用")
        print("=" * 60)

        for step in engine.search_plan:
            script = TOOLS[step["tool"]].script
            params = step["params"]
            params_str = " ".join(f'{k} "{v}"' for k, v in params.items())
            cmd = f'python {script} --query "{args.query}" {params_str}'
            print(f"\n# {step['priority']}: {TOOLS[step['tool']].name}")
            print(cmd)

    # Fact-Check 模式
    if args.fact_check or args.claims_file:
        print("\n" + "=" * 60)
        print("## 事实核查 (Fact-Check)")
        print("=" * 60)

        claims_to_verify = []

        # 从文件读取 claims
        if args.claims_file:
            with open(args.claims_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                claims_to_verify = data.get("key_claims", [])
                print(f"从 {args.claims_file} 读取 {len(claims_to_verify)} 条事实声明")

        if not claims_to_verify:
            print("未提供事实声明，使用 --claims-file 指定")
            print("\n示例 JSON 格式:")
            print(json.dumps({
                "key_claims": [
                    "Farxiga 2024 年销售额 77 亿美元",
                    "Tagrisso 2024 年销售额 65.8 亿美元",
                    "Tozorakimab COPD Phase 3 试验阳性",
                ]
            }, indent=2, ensure_ascii=False))
        else:
            # 调用 fact_check 模块
            script_dir = os.path.dirname(os.path.abspath(__file__))
            fact_check_script = os.path.join(script_dir, "fact_check.py")

            cmd = [
                "python3", fact_check_script,
                "--facts-file", args.claims_file,
                "--format", args.format,
            ]
            if args.output:
                cmd.extend(["--output", args.output])

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                print(result.stdout)
            else:
                print(f"Fact-check 失败：{result.stderr}")


if __name__ == "__main__":
    main()
