# CIDector Research Skill 使用说明

## 概述

`skills/research/` 目录包含 CIDector 的决策引擎和质量控制工具：

| 文件 | 说明 |
|------|------|
| `research.py` | 决策引擎 - 分析问题类型、生成搜索计划 |
| `fact_check.py` | Fact-Check 模块 - 对关键事实进行多源交叉验证 |
| `example_claims.json` | 示例事实声明文件 |
| `README.md` | Research 技能使用说明 |
| `FACT_CHECK_README.md` | Fact-Check 模块详细说明 |

## 快速开始

```bash
# 1. 运行研究分析
python3 skills/research/research.py --query "B7-H3 ADC 竞争格局"

# 2. 创建关键事实声明 (claims.json)

# 3. 运行 Fact-Check 验证关键结论
python3 skills/research/fact_check.py --facts-file claims.json
```

## 使用方式

### 1. 直接使用 research.py

```bash
# 查看分析结果和搜索计划
python3 skills/research/research.py --query "B7-H3 ADC 竞争格局"

# JSON 输出 (适合程序化使用)
python3 skills/research/research.py --query "B7-H3 ADC" --format json

# 生成执行命令
python3 skills/research/research.py --query "..." --auto-execute

# 带 Fact-Check 的完整分析
python3 skills/research/research.py --query "阿斯利康 管线" --fact-check --claims-file claims.json
```

### 2. 使用 fact_check.py

```bash
# 验证单条事实
python3 skills/research/fact_check.py --facts "Farxiga 2024 年销售额 77 亿美元"

# 验证多条事实
python3 skills/research/fact_check.py --facts-file example_claims.json

# 输出报告文件
python3 skills/research/fact_check.py --facts-file claims.json --output report.md

# JSON 格式输出
python3 skills/research/fact_check.py --facts "..." --format json
```

详细 Fact-Check 使用说明参见：[FACT_CHECK_README.md](FACT_CHECK_README.md)

## 问题类型与分析维度

| 问题类型 | 关键词示例 | 推荐分析维度 |
|---------|-----------|-------------|
| **pipeline_deep_dive** | 靶点管线、竞争格局、ADC 全景 | 分子设计、临床定位、安全性、监管策略、中国 vs 全球 |
| **asset_comparison** | 基准资产、对比、头对头 | 头对头数据、安全性对比、给药方案、监管路径 |
| **china_vs_global** | 中国 vs 全球、出海、中美双报 | 首创路径、适应症差异、监管时间窗、出海机会 |
| **failure_analysis** | 失败、终止、撤回、未达终点 | 失败原因、分子教训、患者选择、启示 |
| **clinical_trial_progress** | 临床三期、招募状态 | 试验设计、入组进度、终点、读出时间 |
| **clinical_data_efficacy** | ORR、PFS、临床数据 | ORR/PFS/OS、安全性谱、亚组分析 |
| **industry_news_bd** | BD 交易、授权、首付款 | 交易结构、里程碑、适应症权利 |
| **regulatory_approval** | FDA 批准、突破性疗法 | 审评时间线、适应症、标签 |
| **conference_data** | ASCO、AACR、海报 | 数据成熟度、亚组、对比基准 |
| **company_profile** | 公司概况、管线介绍 | 管线深度、资金状况、合作伙伴 |
| **capital_market** | 财报、公告、IPO | 现金状况、burn rate、催化剂 |

## 决策流程

```
1. 问题分析 → 识别问题类型 (12 种)
2. 实体提取 → 靶点、药物、公司、适应症、分子类型、地理焦点
3. 维度推荐 → 根据问题类型推荐 5 个分析维度
4. 数据源选择 → 根据问题类型选择 3-5 个工具
5. 生成计划 → 输出人类可读的搜索计划
6. 用户确认 → 确认后执行工具调用
7. 补充搜索 → 如有信息缺口，自动补充
8. 生成报告 → 整合所有信息，输出结构化报告
```

## 示例

### 深度管线分析

```bash
python3 skills/research.py --query "B7-H3 (CD276) ADC 竞争格局"
```

输出：
- 问题类型：`pipeline_deep_dive`
- 提取实体：靶点 (CD276)、分子类型 (ADC)
- 推荐维度：分子设计、临床定位、安全性、监管策略、中国 vs 全球
- 搜索计划：clinical_trials + web_search + pubmed + conferences

### 资产对比

```bash
python3 skills/research.py --query "ifinatamab deruxtecan 基准资产分析"
```

输出：
- 问题类型：`asset_comparison`
- 提取实体：药物 (ifinatamab, deruxtecan)
- 推荐维度：头对头数据、安全性对比、给药方案、监管路径、商业化潜力

### 中国 vs 全球

```bash
python3 skills/research.py --query "CLDN18.2 中国 vs 全球管线对比"
```

输出：
- 问题类型：`china_vs_global`
- 提取实体：靶点 (CLDN18)、地理焦点 (Global)
- 推荐维度：首创路径、适应症差异、监管时间窗、临床资源、出海机会

## 报告格式

参考 `CLAUDE.md` 中的报告模板，核心结构：

1. **核心结论** (5-7 条观点鲜明的 bullet)
2. **竞争格局概览** (China vs Global)
3. **重点资产深度分析** (基准资产、中国领先、失败对照)
4. **对比分析** (决策者视角)
5. **战略展望** (6-18 个月)

## Fact-Check 质量控制

分析完成后，运行 Fact-Check 对关键结论进行交叉验证：

```bash
# 创建关键事实声明文件
cat > claims.json << 'EOF'
{
  "claims": [
    "Farxiga 2024 年销售额 77 亿美元",
    "Tagrisso 2024 年销售额 65.8 亿美元"
  ]
}
EOF

# 运行 Fact-Check
python3 skills/research/fact_check.py --facts-file claims.json --output fact_check_report.md

# 或通过 research.py 集成调用
python3 skills/research/research.py --query "..." --fact-check --claims-file claims.json
```

详情参见：[FACT_CHECK_README.md](FACT_CHECK_README.md)

## 设计原则 (来自专业人士 prompt)

**必须做到**:
- 每个关键事实标注来源
- 临床数据注明试验编号 (NCT/CTR)
- 明确点名可能的赢家与输家
- 区分实质性进展与公关噪音
- **分析完成后运行 fact-check 对关键结论进行交叉验证**

**必须避免**:
- 将所有 Phase III 项目视为等同
- 将所有 ADC 视为同质化
- 通用咨询语言
- 只报喜不报忧

**语气要求**:
- 分析性、决断性
- 适合 R&D 领导和战略委员会
