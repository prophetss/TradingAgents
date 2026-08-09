# Data sources used by TradingAgents

This document inventories the external data providers that are currently wired into the project. The count below follows the code-level interpretation of “data source” as an external data provider/API that the framework actually calls.

## Summary

TradingAgents currently integrates 6 external data providers in code:

1. Yahoo Finance
2. Alpha Vantage
3. FRED
4. Polymarket
5. Reddit
6. StockTwits

## Provider inventory

### 1. Yahoo Finance
- Purpose: market data, technical indicators, fundamentals, news, benchmark return lookup, and instrument identity resolution.
- Main implementation: `tradingagents/dataflows/y_finance.py`, `tradingagents/dataflows/yfinance_news.py`
- Why it matters: it is the default provider for core stock data and several analysis categories.

### 2. Alpha Vantage
- Purpose: an alternative vendor for stock data, technical indicators, fundamentals, and news.
- Main implementation: `tradingagents/dataflows/alpha_vantage_common.py`, `tradingagents/dataflows/alpha_vantage*.py`
- Why it matters: it is supported as a configurable fallback/alternative vendor in the routing layer.

### 3. FRED (Federal Reserve Economic Data)
- Purpose: macroeconomic indicators such as rates, inflation, labor, growth, and related series.
- Main implementation: `tradingagents/dataflows/fred.py`
- Why it matters: it is the default macro-data provider in the framework configuration.

### 4. Polymarket
- Purpose: prediction-market probabilities for forward-looking events.
- Main implementation: `tradingagents/dataflows/polymarket.py`
- Why it matters: it supplies market-implied probabilities for news and research agents.

### 5. Reddit
- Purpose: social sentiment data from Reddit posts, used by the sentiment analyst.
- Main implementation: `tradingagents/dataflows/reddit.py`
- Why it matters: it is fetched directly by the sentiment analyst and is not routed through the shared vendor router.

### 6. StockTwits
- Purpose: social sentiment data from StockTwits messages, used by the sentiment analyst.
- Main implementation: `tradingagents/dataflows/stocktwits.py`
- Why it matters: it is also fetched directly by the sentiment analyst and is not routed through the shared vendor router.

## How these are wired in the project

### Shared vendor router
The project has a central dataflow router in `tradingagents/dataflows/interface.py` that groups tools into categories such as:
- `core_stock_apis`
- `technical_indicators`
- `fundamental_data`
- `news_data`
- `macro_data`
- `prediction_markets`

The router maps those categories to providers such as `yfinance`, `alpha_vantage`, `fred`, and `polymarket`.

### Sentiment analysis path
The sentiment analyst uses a different path for social data. It pre-fetches:
- Reddit posts via `fetch_reddit_posts(...)`
- StockTwits messages via `fetch_stocktwits_messages(...)`

These calls are made directly in `tradingagents/agents/analysts/sentiment_analyst.py` rather than through the shared router.

## Default configuration notes

The default configuration in `tradingagents/default_config.py` sets:
- `core_stock_apis`, `technical_indicators`, `fundamental_data`, and `news_data` to use `yfinance`
- `macro_data` to use `fred`
- `prediction_markets` to use `polymarket`

In practice, a normal analysis run will commonly use Yahoo Finance, FRED, Polymarket, Reddit, and StockTwits; Alpha Vantage is available as an optional alternative vendor.

## Notes

This inventory deliberately excludes:
- LLM providers such as OpenAI, Anthropic, Google, etc.
- Local services such as SQLite, memory logs, and cache directories
- CLI announcement endpoints that are unrelated to market data ingestion
