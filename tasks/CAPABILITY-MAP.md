# Capability Map: AKShare Full Support

Status: approved (autonomous mode - user unavailable, recommended defaults taken)

| Module id | Responsibility | Depends on |
|---|---|---|
| symbol-unify | Single source of truth for A-share symbol detection/normalization in `symbol_utils.py`; fix `.SH` reflection-layer and benchmark-map failures | - |
| ohlcv-fix | Fix `close_200_sma` warm-up window (230 calendar days < 200 trading days of history) | - |
| akshare-fundamentals | East Money financial statements + stock overview wired into the 4 fundamental tools | symbol-unify |
| akshare-news | East Money per-stock news wired into `get_news` with date-window filtering | symbol-unify |

Build order: symbol-unify, ohlcv-fix (parallel) -> akshare-fundamentals -> akshare-news

## Decisions (autonomous, documented for review)

1. **Auto-routing for A-share fundamentals/news**, same pattern as
   `core_stock_apis`: when the configured vendor is exactly `yfinance` and the
   symbol carries a `.SH`/`.SZ`/`.SS` suffix, route to akshare. Rationale:
   yfinance returns no data for `.SH`/`.SZ` symbols, so without auto-routing
   A-share fundamentals are permanently `NO_DATA_AVAILABLE` (verified in the
   2026-08-19 real run of `600519.SH`). Explicit `data_vendors` config still
   wins over auto-routing for non-default chains.
2. **Scope = all three modules.** The fix modules are prerequisites for the
   extension modules to be meaningful; the real-run evidence shows
   fundamentals as the largest pain point (all 4 tools returned NO_DATA).
3. **Test env**: uv-managed `.venv` on Python 3.12 (CI-verified version),
   since system Python is 3.14 (unverified) and `python3-venv` is missing.
4. **Look-ahead safety**: fundamental reports with a reporting period after
   `curr_date` are filtered out, matching the project's backtest-fidelity
   contract (`filter_financials_by_date` semantics).
5. **Non-A-share behavior unchanged**: yfinance remains the default for every
   non-A-share symbol; no existing test may break.
