# Fact-Check 模块 - 事实核查与多源交叉验证

> 所属技能：CIDector Research Skill  
> 脚本位置：`skills/research/fact_check.py`  
> 示例文件：`skills/research/example_claims.json`

---

## 概述

Fact-Check 模块用于在研究分析初步完成后，对报告中的**关键结论/事实**进行多源交叉验证，确保数据来源的可靠性和一致性。

这是 CIDector 研究工作流中的**质量控制环节**，用于生成"数据来源透明度总结"附录。

---

## 快速开始

### 方法 1：直接验证单条事实

```bash
python skills/research/fact_check.py --facts "Farxiga 2024 年销售额 77 亿美元" "Tagrisso 2024 年销售额 65.8 亿美元"
```

### 方法 2：从 JSON 文件读取多条事实

```bash
python skills/research/fact_check.py --facts-file skills/research/example_claims.json --output fact_check_report.md
```

### 方法 3：通过 research.py 集成调用

```bash
python skills/research/research.py --query "阿斯利康 管线 专利" --fact-check --claims-file claims.json
```

---

## 核查流程

```
1. 解析事实陈述 
   → 2. 提取验证关键词 
   → 3. 多源搜索验证 
   → 4. 一致性评估 
   → 5. 生成报告
```

---

## 输出格式

### Markdown 报告示例

```markdown
# 事实核查报告

> 生成时间：2026-04-03T15:30:00

## 核查摘要

- 总核查数量：15
- 已验证：8
- 可能为真：4
- 存在冲突：2
- 未验证：1

## 详细核查结果

### 1. Farxiga 2024 年销售额 77 亿美元

- **状态**: 🟡 likely_true
- **类别**: sales_figure
- **关键实体**: {"drugs": ["Farxiga"], "figures": ["77 亿"], "dates": ["2024"]}

**来源**:
  1. [AstraZeneca 2024: Growing into the future](https://www.pharmalive.com/...)
  2. [AstraZeneca's 2024 revenue surges to $54bn](https://druganddeviceworld.com/...)

---

## 数据来源透明度总结

### 已验证事实（至少 2 个独立来源）
- Tagrisso 2024 年销售额 65.8 亿美元：[URL](...)

### 单一来源事实（需进一步验证）
- Farxiga 2024 年销售额 77 亿美元：[URL](...)

### 不确定/冲突信息
- Farxiga 美国仿制药上市时间：来源冲突 (2026 vs 2027)
```

### JSON 输出

```bash
python skills/research/fact_check.py --facts "..." --format json
```

```json
[
  {
    "statement": "Farxiga 2024 年销售额 77 亿美元",
    "category": "sales_figure",
    "status": "likely_true",
    "sources": [
      {
        "title": "AstraZeneca 2024: Growing into the future",
        "url": "https://www.pharmalive.com/...",
        "content": "..."
      }
    ],
    "conflicts": []
  }
]
```

---

## 核查状态说明

| 状态 | 标识 | 说明 |
|------|------|------|
| `verified` | ✅ | 至少 2 个独立可靠来源确认 |
| `likely_true` | 🟡 | 1 个可靠来源支持 |
| `conflicting` | ⚠️ | 不同来源数据存在冲突 |
| `unverified` | ❌ | 无搜索结果 |
| `uncertain` | ❓ | 信息来源模糊 |

---

## 事实分类

模块自动识别以下事实类型：

| 类别 | 识别模式 | 验证策略 |
|------|----------|----------|
| `sales_figure` | 销售额/收入/营收 + 数字 | 搜索药物 + 年份 + revenue/sales |
| `trial_status` | Phase X + 阳性/成功/失败 | 搜索药物 + trial results |
| `patent_date` | 专利 + 到期/expiration | 搜索药物 + patent expiration |
| `regulatory` | FDA/NMPA + 批准 | 搜索药物 + approval |
| `clinical_data` | ORR/PFS/OS + 数值 | 搜索药物 + clinical data |
| `timeline` | 预计/expected + 时间 | 搜索药物/公司 + catalyst |

---

## 在研究工作流中的位置

```
┌─────────────────────────────────────────────────────────────┐
│                    CIDector 研究流程                         │
└─────────────────────────────────────────────────────────────┘

  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ 问题分析  │ →  │ 数据收集  │ →  │ 初步分析  │ →  │ 最终报告  │
  └──────────┘    └──────────┘    └──────────┘    └──────────┘
                                       ↓              ↑
                              ┌──────────────┐      │
                              │  Fact-Check  │ ────┘
                              │ (质量控制)    │
                              └──────────────┘
```

---

## 最佳实践

### 什么样的事实需要核查？

**建议核查**：
- ✅ 投资/BD 决策依赖的关键数据
- ✅ 可能引起争议的结论
- ✅ 单一来源的重要信息
- ✅ 与常识/预期不符的发现

**可不核查**：
- ❌ 公开常识（如"Keytruda 是 PD-1 抑制剂"）
- ❌ 辅助性背景信息
- ❌ 已有多源确认的事实

### 处理冲突信息

当 Fact-Check 报告标注 `conflicting` 时：

1. **检查来源可靠性** — 官方文件 > 行业媒体 > 一般新闻
2. **检查时间戳** — 更新的信息通常更准确
3. **在报告中标注不确定性** — 如"来源 A 称 X，来源 B 称 Y"

### 与 CLAUDE.md 规范对接

根据 CLAUDE.md 第 206-207 行要求：

> **必须做到**:
> - [ ] 每个关键事实标注来源 (URL + 搜索类型)
> - [ ] **分析完成后运行 fact-check 对关键结论进行交叉验证**

Fact-Check 模块自动生成来源列表，可直接用于报告附录：

```markdown
## 附录：数据来源透明度

### 已验证事实（至少 2 个独立来源）
{来自 fact_check_report.md 的 verified 列表}

### 单一来源事实（需进一步验证）
{来自 fact_check_report.md 的 likely_true 列表}

### 不确定/冲突信息
{来自 fact_check_report.md 的 conflicting/unverified 列表}
```

---

## 示例工作流

### 场景：阿斯利康管线分析报告

```bash
# 步骤 1：运行初步研究
python skills/research/research.py --query "阿斯利康 管线 专利" --format json > az_pipeline_analysis.json

# 步骤 2：从分析中提取关键事实（手动或自动）
# 保存为 claims.json
cat > claims.json << 'EOF'
{
  "claims": [
    "Farxiga 2024 年销售额 77 亿美元",
    "Tagrisso 2024 年销售额 65.8 亿美元",
    "Tozorakimab COPD Phase 3 试验阳性"
  ]
}
EOF

# 步骤 3：运行 Fact-Check
python skills/research/fact_check.py --facts-file claims.json --output az_fact_check.md

# 步骤 4：将 Fact-Check 结果整合到最终报告
cat az_pipeline_analysis.md az_fact_check.md > final_report.md
```

---

## 技术细节

### 来源可靠性分级

| 级别 | 来源类型 | 示例 |
|------|----------|------|
| Tier 1 | 官方文件 | AZ 官网 PDF、SEC filing、ClinicalTrials.gov |
| Tier 2 | 权威行业媒体 | FiercePharma、Endpoints、BioCentury |
| Tier 3 | 主流财经媒体 | Reuters、CNBC、Bloomberg |
| Tier 4 | 一般新闻/博客 | 一般新闻网站、博客 |

### 独立性判定

- ✅ 不同域名的来源视为独立
- ✅ 同一媒体集团的不同子品牌视为独立 (如 FiercePharma vs FierceBiotech)
- ❌ 转载/引用不视为独立来源

### 冲突检测算法

1. 提取声明中的关键数字（如"77 亿"）
2. 在来源内容中搜索不同数字
3. 如果找到数量级相同但数值不同的数字，标记为潜在冲突

---

## 限制与注意事项

1. **语言限制** — 目前主要针对中英文搜索优化
2. **时间延迟** — 每条事实需要 3-5 次搜索，约需 30-60 秒
3. **无法访问付费内容** — 如 BioCentury 付费文章
4. **不替代人工核查** — 关键决策仍需人工复核原始来源

---

## 故障排除

### 问题：Fact-Check 结果为"unverified"过多

**可能原因**：
- 搜索关键词不够具体
- 信息来源太新/太旧
- 事实陈述过于模糊

**解决方案**：
- 添加更多实体信息（如具体年份、适应症）
- 调整事实陈述的精确度

### 问题：来源冲突但无法判断

**建议**：
1. 优先采用官方文件（PDF、投资者演示）
2. 优先采用更新的信息
3. 在报告中同时呈现两种说法

---

## 命令参考

```bash
# 基本用法
python skills/research/fact_check.py --facts "事实 1" "事实 2"

# 从文件读取
python skills/research/fact_check.py --facts-file claims.json

# 指定输出文件
python skills/research/fact_check.py --facts-file claims.json --output report.md

# JSON 格式输出
python skills/research/fact_check.py --facts "..." --format json

# 通过 research.py 调用
python skills/research/research.py --query "..." --fact-check --claims-file claims.json
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `--facts`, `-f` | 要验证的事实声明列表 |
| `--facts-file` | 包含事实声明的 JSON 文件 |
| `--output`, `-o` | 输出报告文件路径 |
| `--format` | 输出格式：text/json/markdown (默认 markdown) |

---

## 相关文件

- `skills/research/fact_check.py` - Fact-Check 主脚本
- `skills/research/example_claims.json` - 示例事实声明文件
- `skills/research/research.py` - Research 主技能（集成 Fact-Check 调用）
- `tools/web_search.py` - 网页搜索工具（Fact-Check 依赖）

---

## 更新日志

- 2026-04-03: 初始版本发布
