# Spec: akshare-fundamentals - East Money financial statements

Module: `akshare-fundamentals` (from `tasks/CAPABILITY-MAP.md`)

## Objective

Wire AKShare's East Money fundamental endpoints into the four fundamental
tools so A-share symbols get real financial data instead of
`NO_DATA_AVAILABLE` (verified: the 2026-08-19 real run of `600519.SH` had all
four fundamental tools fail, and the Portfolio Manager's final decision was
made in an "evidence vacuum").

Target AKShare interfaces (East Money family, no API key required):

| Tool | AKShare call | Notes |
|---|---|---|
| `get_fundamentals` | `stock_individual_info_em(symbol)` | Per-stock profile: name, industry, total shares, float shares, market cap |
| `get_balance_sheet` | `stock_balance_sheet_by_report_em(date=period)` filtered to symbol | Statement rows keyed by report period |
| `get_cashflow` | `stock_cash_flow_sheet_by_report_em(date=period)` filtered to symbol | Same |
| `get_income_statement` | `stock_profit_sheet_by_report_em(date=period)` filtered to symbol | Same |

Statement endpoints take a report **period** (`"20240331"` style) rather than
a symbol+range, so the adapter iterates the quarterly/annual periods within
the lookback window and filters rows to the requested symbol. Exact column
names to be verified against the installed akshare version before
implementation (see Open Questions).

## Interface contract (must match `VENDOR_METHODS` registration)

```python
def get_fundamentals(ticker: str, curr_date: str | None = None) -> str
def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str
def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str
def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str
```

Registered in `VENDOR_METHODS` under `akshare`; routed automatically when the
configured vendor chain is exactly `yfinance` and the symbol is an A-share
(same rule as `core_stock_apis`, extended via `symbol-unify`'s shared helper).

## Look-ahead safety

Rows whose report period (`REPORT_DATE` / 报告期) is after `curr_date` are
dropped. A-share disclosures lag the period end (annual reports publish by
Apr 30, Q1 by Apr 30, H1 by Aug 31, Q3 by Oct 31), so period-end filtering is
the conservative choice available from the statement payload itself; the
disclosure-date column (`NOTICE_DATE` / 公告日期) is used when present, and
falls back to period + statutory-max-lag (2 quarters) when absent.

## Output shape

Same header convention as yfinance adapters:

```
# Balance Sheet data for 600519 (quarterly)
# Data retrieved on: YYYY-MM-DD HH:MM:SS

<CSV rows: report period as columns, items as rows>
```

Values are CNY (raw units from East Money: yuan) - state units in the header
so the agent doesn't misread them as USD.

## Success Criteria

1. `route_to_vendor("get_fundamentals", "600519.SH", "2026-08-19")` with
   default `yfinance` config returns a non-NO_DATA string containing company
   name/industry/market-cap fields (mocked at the akshare boundary).
2. All four tools respect `freq` (`annual` -> Dec-31 periods only; `quarterly`
   -> all four periods) and drop report periods after `curr_date`.
3. Non-A-share symbols (`AAPL`) never route to these adapters.
4. Missing dependency raises `VendorNotConfiguredError`; empty/short response
   raises `NoMarketDataError` with a specific `detail`.
5. All existing tests remain green.

## Commands

- Focused: `.venv/bin/python -m pytest tests/test_akshare.py -q`

## Boundaries

- Always: no API key required (East Money is keyless); mock the akshare
  boundary in tests; keep CNY units explicit in headers.
- Ask first: adding forecast/estimate endpoints, macro indicators, or analyst
  ratings (out of scope for this module).
- Never: converting CNY to USD (fabricates an FX rate the framework doesn't
  have); fabricating rows when the statement endpoint returns none.

## Open Questions

- Exact Chinese column names of the three statement endpoints for the
  installed akshare version - resolved by live probing before implementation
  (results recorded in the implementation notes), with the column map
  centralized in one dict so a future akshare rename is a one-line fix.
