# Data sources used by TradingAgents

This document inventories the external data providers that are currently wired into the project. The count below follows the code-level interpretation of “data source” as an external data provider/API that the framework actually calls.

## Summary

TradingAgents currently integrates 7 external data providers in code:

1. Yahoo Finance
2. Alpha Vantage
3. FRED
4. Polymarket
5. Reddit
6. StockTwits
7. AKShare

## Provider inventory

### 1. Yahoo Finance
- Purpose: market data, technical indicators, fundamentals, news, benchmark return lookup, and instrument identity resolution.
- Main implementation: `tradingagents/dataflows/vendors/yahoo/`
- Why it matters: it is the default provider for core stock data and several analysis categories.

### 2. Alpha Vantage
- Purpose: an alternative vendor for stock data, technical indicators, fundamentals, and news.
- Main implementation: `tradingagents/dataflows/vendors/alpha_vantage/`
- Why it matters: it is supported as a configurable fallback/alternative vendor in the routing layer.

### 3. FRED (Federal Reserve Economic Data)
- Purpose: macroeconomic indicators such as rates, inflation, labor, growth, and related series.
- Main implementation: `tradingagents/dataflows/vendors/fred.py`
- Why it matters: it is the default macro-data provider in the framework configuration.

### 4. Polymarket
- Purpose: prediction-market probabilities for forward-looking events.
- Main implementation: `tradingagents/dataflows/vendors/polymarket.py`
- Why it matters: it supplies market-implied probabilities for news and research agents.

### 5. Reddit
- Purpose: social sentiment data from Reddit posts, used by the sentiment analyst.
- Main implementation: `tradingagents/dataflows/vendors/reddit.py`
- Why it matters: it is fetched directly by the sentiment analyst and is not routed through the shared vendor router.

### 6. StockTwits
- Purpose: social sentiment data from StockTwits messages, used by the sentiment analyst.
- Main implementation: `tradingagents/dataflows/vendors/stocktwits.py`
- Why it matters: it is also fetched directly by the sentiment analyst and is not routed through the shared vendor router.

### 7. AKShare
- Purpose: Chinese A-share and China-listed ETF daily OHLCV, technical indicators, East Money financial statements, per-stock news, and China macro indicators (CPI, PPI, LPR, GDP, M2/M1, PMI).
- Main implementation: `tradingagents/dataflows/vendors/akshare.py`, `tradingagents/dataflows/vendors/akshare_macro.py`
- Why it matters: Yahoo Finance has no coverage for the `.SH`/`.SS`/`.SZ` A-share forms, so these symbols auto-route to AKShare when the configured chain does not already include it.

## How these are wired in the project

### Shared vendor router
The project has a central dataflow router in `tradingagents/dataflows/router.py` that groups tools into categories such as:
- `core_stock_apis`
- `technical_indicators`
- `fundamental_data`
- `news_data`
- `macro_data`
- `prediction_markets`

The router maps those categories to providers under `tradingagents/dataflows/vendors/`: `yfinance`, `alpha_vantage`, `sec_edgar`, `fred`, `akshare`, and `polymarket`.

### Sentiment analysis path
The sentiment analyst uses a different path for social data. It pre-fetches:
- Reddit posts via `fetch_reddit_posts(...)`
- StockTwits messages via `fetch_stocktwits_messages(...)`

These calls are made directly in `tradingagents/agents/analysts/sentiment_analyst.py` rather than through the shared router.

## Default configuration notes

The default configuration in `tradingagents/default_config.py` sets:
- `core_stock_apis`, `technical_indicators`, and `news_data` to use `yfinance`
- `fundamental_data` to use `sec_edgar,yfinance`
- `macro_data` to use `fred,akshare`
- `prediction_markets` to use `polymarket`

In practice, a normal analysis run will commonly use Yahoo Finance, FRED, Polymarket, Reddit, and StockTwits; Alpha Vantage is available as an optional alternative vendor, and AKShare serves Chinese A-shares and China macro data.

## Notes

This inventory deliberately excludes:
- LLM providers such as OpenAI, Anthropic, Google, etc.
- Local services such as SQLite, memory logs, and cache directories
- CLI announcement endpoints that are unrelated to market data ingestion
