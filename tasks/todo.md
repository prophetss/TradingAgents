# Tasks: AKShare Full Support

Ordered by dependency (see `tasks/plan.md`). Checkboxes updated as work
completes. Probe/task checkboxes below are reset to `[ ]` and flipped to `[x]`
only as each task actually completes during implementation.

- [x] Task: Probe AKShare API shapes and pin column maps
  - Acceptance: exact column names for `stock_individual_info_em`, the three
    statement endpoints, and `stock_news_em` recorded in `tasks/todo.md`.
  - Verify: probe script output or source inspection, recorded below.
  - Files: (no source changes; notes only)

- [x] Task: symbol-unify - single source of truth for A-share symbols
  - Acceptance: `is_a_share_symbol` / `a_share_code` / `a_share_yahoo_symbol`
    live in `symbol_utils.py`; duplicated `_is_a_share_symbol` bodies deleted;
    benchmark_map has `.SH`; `_fetch_returns` resolves `.SH` -> `.SS`.
  - Verify: `.venv/bin/python -m pytest tests/test_akshare.py tests/test_symbol_utils.py tests/test_symbol_normalization_paths.py -q`
  - Files: `tradingagents/dataflows/symbol_utils.py`, `interface.py`,
    `stockstats_utils.py`, `akshare.py`, `tradingagents/default_config.py`,
    `tradingagents/graph/trading_graph.py`

- [x] Task: ohlcv-fix - trading-day-aware warm-up window
  - Acceptance: `close_200_sma` with `look_back_days=30` returns rows on a
    400-trading-day synthetic series; short lookbacks don't over-fetch.
  - Verify: new regression test in `tests/test_akshare.py` fails on old arithmetic.
  - Files: `tradingagents/dataflows/akshare.py`

- [x] Task: akshare-fundamentals - 4 East Money fundamental adapters
  - Acceptance: all four tools return real-shaped data for `.SH/.SS/.SZ`
    under default config (mocked boundary); look-ahead periods dropped;
    `AAPL` never routes to akshare.
  - Verify: `tests/test_akshare.py` fundamentals test group.
  - Files: `tradingagents/dataflows/akshare.py`, `interface.py`,
    `tests/test_akshare.py`

- [x] Task: akshare-news - East Money news adapter
  - Acceptance: `get_news` for A-shares returns window-filtered articles,
    capped at `news_article_limit`; empty-in-window returns plain text.
  - Verify: `tests/test_akshare.py` news test group.
  - Files: `tradingagents/dataflows/akshare.py`, `interface.py`,
    `tests/test_akshare.py`

- [x] Task: docs & regression sweep
  - Acceptance: README/RUNNING document the new coverage; CHANGELOG entry;
    full pytest + ruff green.
  - Verify: `.venv/bin/python -m pytest -q` and `.venv/bin/python -m ruff check .`
  - Files: `README.md`, `RUNNING.md`, `CHANGELOG.md`

- [x] Task: china-etf-akshare - daily ETF data and indicators
  - Acceptance: Shanghai and Shenzhen ETF symbols are recognized separately
    from regular A-shares, automatically routed to AKShare, normalized to the
    shared OHLCV contract, and supported by existing indicators. East Money
    history failure falls back to Sina.
  - Verify: `tests/test_akshare.py` ETF group and live `510300.SH` smoke check.
  - Scope note: ETF-specific NAV, holdings, scale, creation/redemption, and
    announcements are not implemented yet.
  - Files: `tradingagents/dataflows/symbol_utils.py`,
    `tradingagents/dataflows/akshare.py`, `tradingagents/dataflows/interface.py`,
    `tests/test_akshare.py`, `README.md`, `RUNNING.md`, `CHANGELOG.md`

## Probe notes (Step 0) - VERIFIED against akshare 1.18.92 live

- `stock_news_em(symbol)` -> columns 关键词/新闻标题/新闻内容/发布时间/
  文章来源/新闻链接. **WORKS** (10 rows for 600519).
- `stock_balance_sheet_by_report_em(symbol="SH600519")` -> **signature is
  per-symbol** (NOT per-report-period as older docs suggest). Columns:
  `SECUCODE`, `SECURITY_CODE`, `REPORT_DATE`, `REPORT_TYPE`, `NOTICE_DATE`,
  `MONETARYFUNDS`, `ACCOUNTS_RECE`, `INVENTORY`, `TOTAL_ASSETS`,
  `TOTAL_LIABILITIES`, `TOTAL_EQUITY`, `MINORITY_EQUITY`,
  `TOTAL_PARENT_EQUITY`, ... **WORKS**.
- `stock_profit_sheet_by_report_em(symbol="SH600519")` -> same shape; key
  columns `TOTAL_OPERATE_INCOME`, `OPERATE_INCOME`, `OPERATE_COST`,
  `OPERATE_PROFIT`, `TOTAL_PROFIT`, `NETPROFIT`, `PARENT_NETPROFIT`,
  `BASIC_EPS`, `DILUTED_EPS`. **WORKS**.
- `stock_cash_flow_sheet_by_report_em(symbol="SH600519")` -> same shape; key
  columns `NETCASH_OPERATE`, `NETCASH_INVEST`, `NETCASH_FINANCE`, `END_CCE`.
  **WORKS**.
- `stock_individual_info_em(symbol)` -> **UNSTABLE from this network**
  (push2.eastmoney.com drops python-client connections intermittently;
  datacenter-web.eastmoney.com is reachable). `get_fundamentals` therefore
  sources its overview from the statement payloads + OHLCV instead of this
  endpoint, with the endpoint as best-effort enrichment wrapped in try/except.
- Symbol form for the statement endpoints: `SH600519` / `SZ000001` prefix
  style (exchange + code, no dot).
- Report rows come back newest-first with `NOTICE_DATE` (披露日期) present -
  use it for look-ahead filtering, fall back to `REPORT_DATE` when null.
- Values are in CNY yuan (raw East Money units).

---

## Historical tasks (shipped first slice)

- [x] Add optional AKShare dependency and vendor registration
  - Acceptance: `akshare` is available through the project extra and appears in the vendor map.
  - Verify: focused routing tests.
  - Files: `pyproject.toml`, `tradingagents/dataflows/interface.py`, `tradingagents/default_config.py`
- [x] Implement Chinese A-share OHLCV and indicator adapter
  - Acceptance: normalized output matches existing tool contracts and failures are explicit.
  - Verify: mocked adapter tests.
  - Files: `tradingagents/dataflows/akshare.py`
- [x] Run regression and integration checks
  - Acceptance: focused and full pytest suites pass; no secrets are present.
  - Verify: pytest and git diff inspection.
  - Files: `tests/test_akshare.py`
