---
name: fact-check
description: 对关键事实声明进行多源交叉验证。用于报告完成后的质量控制。
argument-hint: <事实声明，如 "Farxiga 2024年销售额77亿美元"> 或 <--file claims.json>
allowed-tools: [Bash, Read, Write]
---

# /fact-check — 事实核查

对关键事实声明进行多源交叉验证，输出验证状态和来源报告。

## 用法

```
/fact-check Farxiga 2024年销售额77亿美元
/fact-check --file claims.json
```

## 执行步骤

用户提供的参数为: $ARGUMENTS

### 方式一：直接验证事实声明

如果参数是一段文字（不以 `--file` 开头），将其作为事实声明直接验证：

```bash
cd "<PLUGIN_ROOT>" && python3 skills/research/fact_check.py --facts "$ARGUMENTS"
```

### 方式二：从 JSON 文件验证

如果参数以 `--file` 开头，提取文件路径并从文件读取事实列表：

```bash
cd "<PLUGIN_ROOT>" && python3 skills/research/fact_check.py --facts-file "<filepath>"
```

JSON 文件格式：
```json
{
  "claims": [
    "Farxiga 2024 年销售额 77 亿美元",
    "Tagrisso 2024 年销售额 65.8 亿美元"
  ]
}
```

### 输出

展示验证结果，按可信度分类：
- **已验证** — 至少 2 个独立来源确认
- **可能为真** — 1 个可靠来源
- **存在冲突** — 不同来源数据不一致
- **未验证** — 无搜索结果

如果需要保存报告：

```bash
cd "<PLUGIN_ROOT>" && python3 skills/research/fact_check.py --facts "$ARGUMENTS" --output fact_check_report.md
```
