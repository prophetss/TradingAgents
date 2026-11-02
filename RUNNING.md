# TradingAgents(akshare 版)完整运行说明

> 本文档面向本仓库（TradingAgents-akshare fork），覆盖从环境准备、安装、API Key 配置、A股数据源（AKShare）接入，到 CLI / Python / Docker 三种运行方式的完整流程，以及结果输出、断点恢复、常见问题排查。

---

## 目录

1. [项目简介](#1-项目简介)
2. [环境要求](#2-环境要求)
3. [安装步骤](#3-安装步骤)
4. [API Key 配置](#4-api-key-配置)
5. [AKShare A股数据源（本 fork 特色）](#5-akshare-a股数据源本-fork-特色)
6. [运行方式一：交互式 CLI（推荐）](#6-运行方式一交互式-cli推荐)
7. [运行方式二：Python 编程调用](#7-运行方式二python-编程调用)
8. [运行方式三：Docker](#8-运行方式三docker)
9. [支持的市场与股票代码](#9-支持的市场与股票代码)
10. [输出结果说明](#10-输出结果说明)
11. [持久化与断点恢复](#11-持久化与断点恢复)
12. [配置项速查（TRADINGAGENTS_\* 环境变量）](#12-配置项速查tradingagents_-环境变量)
13. [开发与测试](#13-开发与测试)
14. [常见问题（FAQ）](#14-常见问题faq)
15. [免责声明](#15-免责声明)

---

## 1. 项目简介

TradingAgents 是一个多智能体 LLM 金融交易框架，模拟真实交易公司的分工协作：

| 团队 | 角色 | 职责 |
|---|---|---|
| **分析师团队** | 基本面分析师 / 情绪分析师 / 新闻分析师 / 技术分析师 | 评估公司财务、社媒情绪、宏观新闻、技术指标 |
| **研究团队** | 多头研究员 / 空头研究员 / 研究经理 | 结构化辩论，权衡收益与风险 |
| **交易团队** | 交易员 | 综合报告决定交易时机与规模 |
| **风险管理** | 激进 / 中性 / 保守分析师 | 评估波动性、流动性等风险 |
| **投资组合** | 投资组合经理 | 最终批准或否决交易提案 |

本 fork 的主要差异：**中国大陆 A 股及中国 ETF 的日线行情与技术指标路由到 AKShare**，其余市场仍走 Yahoo Finance。

---

## 2. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux / macOS / Windows 均可 |
| Python | **3.10 ~ 3.13**（推荐 3.12） |
| 网络 | 需访问 LLM 提供商 API；使用 yfinance/新闻源时建议可访问国际网络 |
| 磁盘 | 运行产物写入 `~/.tradingagents/`（缓存、日志、记忆），占用很小 |

核心依赖（安装时自动处理）：`langgraph`、`langchain-*`、`yfinance`、`pandas`、`stockstats`、`typer`、`rich`、`questionary` 等，完整列表见 `pyproject.toml`。

---

## 3. 安装步骤

### 3.1 获取代码

```bash
git clone <本仓库地址>
cd TradingAgents-akshare
```

### 3.2 创建虚拟环境（任选其一）

**conda：**

```bash
conda create -n tradingagents python=3.12
conda activate tradingagents
```

**venv：**

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 3.3 安装基础包

```bash
pip install .
```

### 3.4 可选扩展

```bash
pip install ".[akshare]"   # A股数据源支持（本 fork 推荐）
pip install ".[bedrock]"   # Amazon Bedrock 支持
pip install ".[dev]"       # 开发用：pytest + ruff
```

全部安装：

```bash
pip install ".[akshare,bedrock,dev]"
```

### 3.5 验证安装

```bash
tradingagents --help        # 已安装命令
python -m cli.main --help   # 从源码直接运行
```

> 若命令找不到，确认虚拟环境已激活，或使用 `python -m cli.main` 替代。

---

## 4. API Key 配置

### 4.1 快捷方式：.env 文件

```bash
cp .env.example .env
# 编辑 .env，填入你使用的 LLM 提供商 Key
```

`.env` 会被自动加载（python-dotenv），无需手动 export。

### 4.2 LLM 提供商 Key 一览（任选其一即可运行）

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # 通义千问 - 国际站
export DASHSCOPE_CN_API_KEY=...    # 通义千问 - 中国站
export ZHIPU_API_KEY=...           # 智谱 GLM - 国际站 (Z.AI)
export ZHIPU_CN_API_KEY=...        # 智谱 GLM - 中国站 (bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax - 国际站
export MINIMAX_CN_API_KEY=...      # MiniMax - 中国站
export OPENROUTER_API_KEY=...      # OpenRouter 聚合
export MISTRAL_API_KEY=...         # Mistral
export MOONSHOT_API_KEY=...        # Kimi
export GROQ_API_KEY=...            # Groq
export NVIDIA_API_KEY=...          # NVIDIA NIM
```

### 4.3 数据源 Key（可选但推荐）

```bash
export FRED_API_KEY=...            # 宏观数据（免费申请: fred.stlouisfed.org）
export ALPHA_VANTAGE_API_KEY=...   # 备用行情/基本面/新闻数据源
```

### 4.4 特殊部署

| 场景 | 做法 |
|---|---|
| **Azure OpenAI** | `cp .env.enterprise.example .env.enterprise`，填 `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT_NAME` |
| **AWS Bedrock** | `pip install ".[bedrock]"`；配置 AWS 凭证（env / `~/.aws/credentials` / IAM 角色）+ `AWS_DEFAULT_REGION`；或用 `AWS_BEARER_TOKEN_BEDROCK` API-Key 认证（优先级更高）。模型形如 `us.anthropic.claude-opus-4-8-v1:0` |
| **本地 Ollama** | `llm_provider: "ollama"`，默认 `http://localhost:11434/v1`，远端用 `OLLAMA_BASE_URL` 指定；先 `ollama pull <模型名>` |
| **任意 OpenAI 兼容端点**（vLLM / LM Studio / llama.cpp / 中转） | `llm_provider: "openai_compatible"`，`backend_url`（或 `TRADINGAGENTS_LLM_BACKEND_URL`）设为如 `http://localhost:8000/v1`；本地服务无需 Key，需鉴权时设 `OPENAI_COMPATIBLE_API_KEY` |

---

## 5. AKShare A股数据源（本 fork 特色）

### 5.1 安装

```bash
pip install ".[akshare]"
```

### 5.2 配置

在程序化配置中把行情与技术指标路由到 akshare：

```python
config = DEFAULT_CONFIG.copy()
config["data_vendors"] = {
    "core_stock_apis": "akshare",        # 日线行情
    "technical_indicators": "akshare",   # 技术指标
}
```

### 5.3 数据覆盖（v0.3.2 起）

| 数据类别 | AKShare 覆盖情况 |
|---|---|
| 日线行情 OHLCV | ✅ A 股东财（qfq 前复权）；ETF 东财优先、Sina 回退 |
| 技术指标（12 项） | ✅ A 股和 ETF，含 200 SMA（已修复预热窗口） |
| 基本面三表 + 概览 | ✅ 东财报表（人民币计价，按披露日过滤未来数据） |
| 个股新闻 | ✅ 东财新闻流（按时间窗口过滤） |
| 宏观数据 / 情绪（Reddit/StockTwits） | ✅ 宏观：FRED 优先（需 Key）+ AKShare 中国宏观（CPI/LPR/M2/GDP/PMI）回退；情绪仍走原路径 |
| ETF 专属净值、持仓、规模、申赎和公告 | ❌ 尚未接入 |

**自动路由规则**：当某类别的 vendor 链中没有 `akshare`，且符号带 `.SH`/`.SS`/`.SZ` 后缀时，行情、指标、基本面、新闻四类自动改走 akshare（因 Yahoo/Alpha Vantage 对 A 股无数据）；链中已显式包含 `akshare` 时按配置顺序执行。

### 5.4 代码识别规则

- `.SH` / `.SS` / `.SZ` 后缀（如 `600519.SH`、`600519.SS`、`000001.SZ`）识别为 A 股；ETF 示例为 `510300.SH`、`588000.SH`、`159915.SZ`。
- ETF 作为 stock 类型运行，不进入 crypto pipeline；`510300.SH` 等符号会自动路由到 AKShare ETF 日线接口。
- 纯 6 位数字代码（如 `600519`）可在显式选择 `akshare` vendor 时使用。
- 反思层会自动把 `.SH` 换算为 Yahoo 的 `.SS` 形式做已实现收益回取；alpha 基准对两个上海后缀都用上证综指 `000001.SS`。
- 支持有序回退链：`"yfinance,akshare"`（依次尝试）。

### 5.5 完整 vendor 类别

`tradingagents/default_config.py` 中的 `data_vendors`：

| 类别 | 默认 | 可选值 |
|---|---|---|
| `core_stock_apis` | yfinance | akshare / alpha_vantage / yfinance |
| `technical_indicators` | yfinance | akshare / alpha_vantage / yfinance |
| `fundamental_data` | sec_edgar,yfinance | sec_edgar / akshare（A股）/ alpha_vantage / yfinance |
| `news_data` | yfinance | akshare（A股）/ alpha_vantage / yfinance |
| `macro_data` | fred,akshare | fred（需 `FRED_API_KEY`）/ akshare（中国宏观，免 Key） |
| `prediction_markets` | polymarket | polymarket（免 Key） |

工具级覆盖（优先于类别级）：`config["tool_vendors"] = {"get_stock_data": "akshare"}`。

> Reddit / StockTwits 情绪数据由情绪分析师直接拉取，不走共享路由。

---

## 6. 运行方式一：交互式 CLI（推荐）

### 6.1 启动

```bash
tradingagents          # pip 安装后的命令
python -m cli.main     # 从源码运行（等价）
```

### 6.2 交互流程

CLI 会依次引导你选择（`.env` 中已设置对应项的会自动跳过）：

1. **Ticker** —— 输入股票代码（如 `600519.SH`、`AAPL`、`BTC-USD`）
2. **分析日期** —— 格式 `YYYY-MM-DD`
3. **LLM 提供商** —— openai / google / anthropic / deepseek / ollama / openai_compatible 等
4. **深度思考模型（deep_think_llm）** —— 复杂推理用，默认 `gpt-5.5`
5. **快速思考模型（quick_think_llm）** —— 轻量任务用，默认 `gpt-5.4-mini`
6. **分析师组合** —— 市场技术 / 情绪 / 新闻 / 基本面（可多选）
7. **研究深度** —— 辩论轮数（`max_debate_rounds`）
8. **风险讨论深度** —— `max_risk_discuss_rounds`
9. **输出语言** —— 报告语言（内部辩论始终为英文以保证推理质量）

之后终端会实时展示各 Agent 状态、工具调用与报告生成进度。

### 6.3 命令行参数

```bash
tradingagents analyze --checkpoint          # 开启断点续跑（本次运行）
tradingagents analyze --no-checkpoint       # 关闭断点续跑
tradingagents analyze --clear-checkpoints   # 运行前清空所有已存检查点
```

### 6.4 免交互（无人值守）运行

在 `.env` 中预先设好关键项，CLI 将跳过对应交互步骤：

```bash
TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-reasoner
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-chat
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
```

---

## 7. 运行方式二：Python 编程调用

### 7.1 最小示例

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

### 7.2 自定义配置示例（DeepSeek + AKShare + 中文报告）

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-reasoner"   # 复杂推理
config["quick_think_llm"] = "deepseek-chat"      # 快速任务
config["output_language"] = "Chinese"            # 报告输出语言
config["max_debate_rounds"] = 2
config["max_risk_discuss_rounds"] = 1
config["data_vendors"] = {
    "core_stock_apis": "akshare",
    "technical_indicators": "akshare",
}

ta = TradingAgentsGraph(debug=True, config=config)

# A股：600519.SH = 贵州茅台
_, decision = ta.propagate("600519.SH", "2026-08-15")
print(decision)
```

### 7.3 保存报告到磁盘

```python
final_state, decision = ta.propagate("600519.SH", "2026-08-15")

# 生成与 CLI 相同的 markdown 报告树，默认写入 ~/.tradingagents/logs/reports/<TICKER>_<时间戳>/
ta.save_reports(final_state, "600519.SH")
```

### 7.4 复盘记忆（可选）

```python
# 参数为该笔持仓的实际收益率，用于生成反思并写入记忆日志
ta.reflect_and_remember(1000)
```

### 7.5 直接运行仓库自带脚本

```bash
python main.py    # 默认分析 NVDA / 2024-05-10，可自行编辑
```

`main.py` 已自动应用 `TRADINGAGENTS_*` 环境变量覆盖，可纯靠 `.env` 切换模型/端点而不改代码。

### 7.6 加密货币

```python
# CLI 会根据代码自动检测；编程调用时显式传 asset_type
_, decision = ta.propagate("BTC-USD", "2026-01-15", asset_type="crypto")
```

---

## 8. 运行方式三：Docker

### 8.1 准备

```bash
cp .env.example .env   # 填入 API Key
```

### 8.2 标准运行

```bash
docker compose run --rm tradingagents
```

### 8.3 本地模型（Ollama profile）

```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

该 profile 自动设置 `TRADINGAGENTS_LLM_PROVIDER=ollama` 和 `OLLAMA_BASE_URL=http://ollama:11434/v1`。

### 8.4 说明

- 镜像基于 `python:3.12-slim`，非 root 用户运行，入口为 `tradingagents` 命令。
- 运行数据（缓存 / 日志 / 记忆）持久化在 named volume `tradingagents_data`（容器内 `/home/appuser/.tradingagents`）。
- 如需 A 股 AKShare 支持，需在 `Dockerfile` 中改为 `pip install ".[akshare]"` 后重新构建。

---

## 9. 支持的市场与股票代码

使用带交易所后缀的 Yahoo Finance 风格代码；A 股走 AKShare 时用 `.SH/.SZ`：

| 市场 | 示例 |
|---|---|
| 美国 | `AAPL`、`SPY`、`NVDA` |
| 中国 A 股（AKShare） | `600519.SH`（贵州茅台）、`000001.SZ`（平安银行） |
| 中国 ETF（AKShare） | `510300.SH`（沪深300 ETF）、`588000.SH`（科创50 ETF）、`159915.SZ`（创业板 ETF） |
| 中国 A 股（Yahoo） | `600519.SS` |
| 香港 | `0700.HK` |
| 日本 | `7203.T` |
| 伦敦 | `AZN.L` |
| 印度 | `RELIANCE.NS`、`RELIANCE.BO` |
| 加拿大 | `SHOP.TO` |
| 澳大利亚 | `BHP.AX` |
| 加密货币 | `BTC-USD`、`ETH-USD` |

公司身份与 alpha 基准按市场自动解析（如 A 股 `.SZ` → `399001.SZ`，美国 → `SPY`，可通过 `benchmark_ticker` 全局覆盖）。

---

## 10. 输出结果说明

### 10.1 CLI 运行产物

CLI 将结果写入：

```
~/.tradingagents/logs/<TICKER>/<YYYY-MM-DD>/
├── message_tool.log        # 消息与工具调用日志
└── reports/
    ├── 1_analysts/         # 各分析师报告 (market.md / sentiment.md / news.md / fundamentals.md)
    ├── 2_research/         # 多空辩论 (bull.md / bear.md / manager.md)
    ├── 3_trading/          # 交易员方案
    ├── 4_risk/             # 风险团队讨论
    ├── 5_portfolio/        # 投资组合经理最终决策
    └── complete_report.md  # 汇总报告
```

可通过 `TRADINGAGENTS_RESULTS_DIR` 改根目录。仓库内的 `reports/` 目录即一次实际运行的示例。

### 10.2 决策日志（始终开启）

每次完成的运行会追加决策到：

```
~/.tradingagents/memory/trading_memory.md
```

下次分析同一标的时，框架会自动获取已实现收益（原始收益 + 相对基准的 alpha）、生成反思段落，并注入投资组合经理的提示词，实现"从错误中学习"。路径可用 `TRADINGAGENTS_MEMORY_LOG_PATH` 覆盖。

---

## 11. 持久化与断点恢复

### 11.1 开启 checkpoint（默认关闭）

```bash
# CLI
tradingagents analyze --checkpoint

# 或 .env
TRADINGAGENTS_CHECKPOINT_ENABLED=true

# 或 Python
config["checkpoint_enabled"] = True
```

### 11.2 工作机制

- 开启后 LangGraph 在**每个节点执行后**保存状态到 SQLite。
- 崩溃/中断后再次以相同 ticker + 日期 + 图结构运行，会看到日志 `Resuming from step N for <TICKER> on <date>`，从最后成功的步骤继续，而非从头开始。
- 检查点身份包含所选分析师、辩论/风险轮数、资产类型——配置不同不会误续。
- 运行成功完成后检查点自动清除。

### 11.3 存储位置与管理

```
~/.tradingagents/cache/checkpoints/<TICKER>.db     # 可用 TRADINGAGENTS_CACHE_DIR 覆盖基目录
```

```bash
tradingagents analyze --clear-checkpoints   # 清空全部检查点，强制全新开始
```

### 11.4 无头/API 运行的报告输出

`TradingAgentsGraph.save_reports(final_state, ticker)` 生成与 CLI 完全一致的报告树，适合脚本化、服务化集成。

---

## 12. 配置项速查（TRADINGAGENTS_* 环境变量）

写入 `.env` 即可覆盖 `tradingagents/default_config.py` 的默认值，类型自动转换（`true`/`3` 等按默认值类型解析，非法值会在启动时报错而非静默回退）：

| 环境变量 | 对应配置 | 说明 |
|---|---|---|
| `TRADINGAGENTS_LLM_PROVIDER` | `llm_provider` | LLM 提供商 |
| `TRADINGAGENTS_DEEP_THINK_LLM` | `deep_think_llm` | 深度思考模型 |
| `TRADINGAGENTS_QUICK_THINK_LLM` | `quick_think_llm` | 快速思考模型 |
| `TRADINGAGENTS_LLM_BACKEND_URL` | `backend_url` | 自定义 API 端点 |
| `TRADINGAGENTS_OUTPUT_LANGUAGE` | `output_language` | 报告输出语言 |
| `TRADINGAGENTS_MAX_DEBATE_ROUNDS` | `max_debate_rounds` | 多空辩论轮数 |
| `TRADINGAGENTS_MAX_RISK_ROUNDS` | `max_risk_discuss_rounds` | 风险讨论轮数 |
| `TRADINGAGENTS_CHECKPOINT_ENABLED` | `checkpoint_enabled` | 断点续跑开关 |
| `TRADINGAGENTS_BENCHMARK_TICKER` | `benchmark_ticker` | alpha 基准（覆盖自动检测） |
| `TRADINGAGENTS_TEMPERATURE` | `temperature` | 采样温度（降低可减小波动） |
| `TRADINGAGENTS_LLM_MAX_RETRIES` | `llm_max_retries` | SDK 重试次数（应对 429 限流） |
| `TRADINGAGENTS_GOOGLE_THINKING_LEVEL` | `google_thinking_level` | Gemini 思考深度 |
| `TRADINGAGENTS_OPENAI_REASONING_EFFORT` | `openai_reasoning_effort` | OpenAI 推理力度 |
| `TRADINGAGENTS_ANTHROPIC_EFFORT` | `anthropic_effort` | Claude effort |
| `TRADINGAGENTS_RESULTS_DIR` | `results_dir` | 报告输出根目录 |
| `TRADINGAGENTS_CACHE_DIR` | `data_cache_dir` | 缓存/检查点目录 |
| `TRADINGAGENTS_MEMORY_LOG_PATH` | `memory_log_path` | 决策日志路径 |

---

## 13. 开发与测试

### 13.1 安装开发依赖

```bash
pip install ".[dev]"
```

### 13.2 运行测试

```bash
pytest                    # 全量（testpaths=tests）
pytest -m unit            # 仅快速单元测试
pytest -m smoke           # 冒烟测试
pytest tests/test_akshare.py   # 单个文件
```

测试标记：`unit`（快速隔离单元测试）、`integration`（需外部服务）、`smoke`（快速健全性检查）。

### 13.3 代码检查

```bash
ruff check .              # lint（line-length=100, py310 目标）
```

### 13.4 目录结构速览

```
tradingagents/
├── default_config.py    # 全部默认配置 + 环境变量覆盖机制
├── reporting.py         # 报告树写出（CLI 与 API 共用）
├── agents/              # 各 Agent 提示词与实现
├── dataflows/           # 数据源路由 router.py + vendors/（yahoo / akshare / alpha_vantage / sec_edgar / fred / polymarket / reddit / stocktwits）
├── graph/               # LangGraph 编排（trading_graph.py 为主入口）
└── llm_clients/         # 各 LLM 提供商客户端
cli/                     # Typer 交互式命令行
tests/                   # pytest 测试套件
main.py                  # 编程调用示例脚本
```

---

## 14. 常见问题（FAQ）

**Q1：运行结果每次都不一样，是 bug 吗？**
不是。LLM 采样本身非确定（推理型模型差异更大），且新闻 / StockTwits / Reddit 是实时数据。降低波动的方法：设置 `TRADINGAGENTS_TEMPERATURE=0.0`，或改用非推理模型（CLI 里选手动输入自定义模型 ID）。详见 README "Reproducibility" 一节。

**Q2：遇到 429 限流导致运行中断？**
在 `.env` 设置 `TRADINGAGENTS_LLM_MAX_RETRIES=6`（或更高），SDK 会自动重试。

**Q3：国内网络访问 LLM / yfinance 超时？**
可配置代理：

```bash
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897
```

或改用国内可达的提供商（DeepSeek / 通义千问中国站 / 智谱中国站 / MiniMax 中国站）+ AKShare 数据源，基本无需代理。

**Q4：A 股数据拉取失败？**
1. 确认已 `pip install ".[akshare]"`；
2. 行情/指标：确认代码后缀为 `.SH`/`.SS`/`.SZ`（自动路由 akshare）；或显式设 `data_vendors` 指向 `akshare`；
3. 基本面/新闻：v0.3.2 起自动路由 akshare（东财接口，无需配置）；
4. **网络层已内置容错**：东财接口断连会自动重试 3 次；行情域名（push2his）完全不可达时自动切换新浪备用源，无需人工干预；
5. 若整机网络受限，可设代理 `export https_proxy=http://127.0.0.1:7897` 后重跑；
6. 想要自动回退可用 `"yfinance,akshare"` 链。

**Q5：CLI 提示找不到 `tradingagents` 命令？**
虚拟环境未激活，或用 `python -m cli.main` 直接从源码运行。

**Q6：如何让报告输出中文？**
`.env` 设 `TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese`，或交互界面选择，或 `config["output_language"] = "Chinese"`。

**Q7：Windows 下交互界面报错？**
需要真实控制台缓冲，请在 Windows Terminal / PowerShell / cmd 中运行，不要在管道或嵌入式终端中启动。

**Q8：检查点续跑用不上 / 总是 "Starting fresh"？**
thread id 由 ticker + 日期 + 图结构（所选分析师、辩论/风险轮数、资产类型）共同决定——任一变化都会视为新运行。保持配置一致即可续跑；也可 `--clear-checkpoints` 强制重置。

---

## 15. 免责声明

本项目仅用于研究目的，不构成任何金融、投资或交易建议。交易表现受所选模型、温度、交易周期、数据质量等诸多因素影响。
