---
name: research-plan
description: 生成生物医药竞争情报研究计划。分析问题类型、提取实体、推荐数据源和分析维度。
argument-hint: <研究问题，如 "B7-H3 ADC 竞争格局">
allowed-tools: [Bash, Read]
---

# /research-plan — 研究计划生成器

根据用户输入的研究问题，调用 CIDector 决策引擎生成结构化研究计划。

## 用法

```
/research-plan B7-H3 ADC 竞争格局
/research-plan CLDN18.2 中国 vs 全球管线对比
/research-plan 百利天恒 BD 交易分析
```

## 执行步骤

用户提供的参数为: $ARGUMENTS

1. 确定插件根目录（本文件位于 `skills/research-plan/SKILL.md`，根目录是上两级）
2. 在插件根目录下运行决策引擎：

```bash
cd "<PLUGIN_ROOT>" && python3 skills/research/research.py --query "$ARGUMENTS"
```

3. 展示输出的研究计划，包括：
   - 问题类型分类
   - 提取的实体（靶点、药物、公司、适应症）
   - 推荐分析维度
   - 搜索计划（工具 + 参数 + 优先级）

4. 询问用户是否要执行该研究计划。如果确认，按计划依次调用工具并汇总结果。

## JSON 输出模式

如果需要程序化使用，可加 `--format json`：

```bash
cd "<PLUGIN_ROOT>" && python3 skills/research/research.py --query "$ARGUMENTS" --format json
```
