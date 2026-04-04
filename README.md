# CIDector — 生物医药竞争情报 Deep Research Agent

多源数据采集与分析工具集，专为生物医药竞争情报研究设计。覆盖 ClinicalTrials.gov、PubMed、中国 CDE、Tavily 网页搜索、学术会议摘要、资本市场公告等数据源。

## 功能

- **临床试验搜索** — ClinicalTrials.gov REST API v2
- **学术文献搜索** — PubMed / NCBI E-utilities
- **网页搜索** — Tavily API（覆盖 Fierce Biotech、Endpoints 等行业媒体）
- **中国临床试验** — CDE / ChinaDrugTrials / ChiCTR
- **资本市场公告** — 上交所 / 港交所
- **学术会议** — AACR / ASCO / ESMO 摘要搜索
- **研究决策引擎** — 自动分析问题类型、推荐数据源
- **事实核查** — 多源交叉验证关键事实

## 安装方式

### 方式一：Claude Code Plugin（推荐）

在 Claude Code 终端中运行：

```bash
/plugin install yourname/CIDector@github
```

安装后运行一次 setup：

```bash
bash ~/.claude/plugins/installed/cidector/setup.sh
```

编辑 `.env` 填写 API keys：

```bash
vim ~/.claude/plugins/installed/cidector/.env
```

安装完成后，CIDector 会在你提问生物医药相关问题时自动激活。也可以使用 slash commands：

```
/research-plan B7-H3 ADC 竞争格局
/fact-check Farxiga 2024年销售额77亿美元
```

### 方式二：Git Clone 本地使用

```bash
git clone https://github.com/yourname/CIDector.git
cd CIDector
pip3 install -r requirements.txt
cp .env.example .env
# 编辑 .env 填写 API keys
```

在项目目录下启动 Claude Code，`CLAUDE.md` 会自动加载：

```bash
cd CIDector
claude
```

## 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `TAVILY_API_KEY` | 是 | Tavily 搜索 API key ([获取](https://tavily.com)) |
| `NCBI_EMAIL` | 否 | PubMed 请求所需邮箱 |
| `NCBI_API_KEY` | 否 | PubMed API key（提升速率限制到 10 req/s） |

可以通过 `.env` 文件或 shell 环境变量设置。

## 项目结构

```
CIDector/
├── .claude-plugin/
│   └── plugin.json            # Claude Code 插件元数据
├── skills/
│   ├── cidector/
│   │   └── SKILL.md           # 主 skill（自动触发）
│   ├── research-plan/
│   │   └── SKILL.md           # /research-plan 命令
│   ├── fact-check/
│   │   └── SKILL.md           # /fact-check 命令
│   └── research/
│       ├── research.py        # 决策引擎
│       └── fact_check.py      # 事实核查模块
├── tools/
│   ├── search_clinical_trials.py
│   ├── search_pubmed.py
│   ├── web_search.py
│   ├── search_china_trials.py
│   ├── search_stock_disclosure.py
│   ├── search_conferences.py
│   └── fetch_page.py
├── utils/
│   ├── http_client.py         # 异步 HTTP 客户端
│   ├── cache.py               # SQLite 缓存
│   └── parsers.py             # 解析工具
├── reports/                   # 生成的报告输出目录
├── CLAUDE.md                  # 本地开发用 system prompt
├── setup.sh                   # 一键安装脚本
├── requirements.txt           # Python 依赖
└── .env.example               # 环境变量模板
```

## 工具使用示例

```bash
# 临床试验
python3 tools/search_clinical_trials.py --query "B7H4 ADC" --phase "Phase 3"

# PubMed
python3 tools/search_pubmed.py --query "CLDN18.2 clinical trial" --sort pub_date

# 网页搜索
python3 tools/web_search.py --query "ADC approvals 2026" --days 90

# 中国试验
python3 tools/search_china_trials.py --query "百利天恒" --source cde

# 研究计划
python3 skills/research/research.py --query "B7-H3 ADC 竞争格局"

# 事实核查
python3 skills/research/fact_check.py --facts "Farxiga 2024年销售额77亿美元"
```

## License

MIT
