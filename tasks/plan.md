# Implementation Plan: AKShare Full Support

> Supersedes the initial AKShare vertical-slice spec (OHLCV + indicators,
> shipped and verified). This plan implements `tasks/CAPABILITY-MAP.md`.
> Module specs: `SPEC-symbol-unify.md`, `SPEC-ohlcv-fix.md`,
> `SPEC-akshare-fundamentals.md`, `SPEC-akshare-news.md`.

## Component overview & order

```
symbol-unify (foundation)
   │
   ├──> ohlcv-fix (independent bug fix, no coupling)
   │
   ├──> akshare-fundamentals
   │
   └──> akshare-news
```

Verification checkpoint between each module: focused pytest green before the
next module starts.

## Step 0: Probe AKShare API shapes (blocking prerequisite)

Before implementing fundamentals/news, run the installed akshare against real
endpoints to pin down exact column names:

- `stock_individual_info_em(symbol="600519")`
- `stock_balance_sheet_by_report_em(date="20240331")` (check columns)
- `stock_cash_flow_sheet_by_report_em(date="20240331")`
- `stock_profit_sheet_by_report_em(date="20240331")`
- `stock_news_em(symbol="600519")`

Record the column names in this plan; the adapters centralize their maps so a
future akshare rename is a one-line fix. If the network is unavailable, fall
back to akshare's source code inspection (the library ships the parsing logic).

## Module 1: symbol-unify

**Files**: `tradingagents/dataflows/symbol_utils.py` (add helpers),
`tradingagents/dataflows/interface.py` (delegate),
`tradingagents/dataflows/stockstats_utils.py` (delegate),
`tradingagents/dataflows/akshare.py` (delegate),
`tradingagents/default_config.py` (benchmark_map `.SH`),
`tradingagents/graph/trading_graph.py` (`.SH` -> `.SS` in `_fetch_returns`).

**Key changes**:
1. `symbol_utils.is_a_share_symbol(symbol)` - accepts `.SH/.SS/.SZ` suffixed
   (9 chars, 6 digits). Case-insensitive.
2. `symbol_utils.a_share_code(symbol)` - returns the 6-digit code or `None`.
3. `symbol_utils.a_share_yahoo_symbol(symbol)` - `.SH` -> `.SS` mapping for
   the reflection layer (Yahoo's A-share convention).
4. Delete both duplicated `_is_a_share_symbol` bodies; import from symbol_utils.
5. `benchmark_map` gains `".SH": "000001.SS"`.
6. `_fetch_returns` resolves A-share symbols via `a_share_yahoo_symbol`.

**Risk**: `is_a_share_symbol` now accepting `.SS` changes routing for `.SS`
symbols from yfinance (broken, no data) to akshare (working). This is the
intended fix, verified against the real-run evidence.

**Mitigation**: the identity layer (`normalize_symbol`) stays untouched - it's
for Yahoo-symbol canonicalization; the new helpers are for *vendor routing*.
Existing test `test_symbol_normalization_paths.py` covers identity, not
routing, and must stay green.

## Module 2: ohlcv-fix

**Files**: `tradingagents/dataflows/akshare.py` only.

Trading-day-aware warm-up window: `ceil((warmup + lookback + 10) * 365/242)`
calendar days. Regression test with a synthetic 400-trading-day series must
fail on the old `+30` arithmetic.

## Module 3: akshare-fundamentals

**Files**: `tradingagents/dataflows/akshare.py` (4 adapters + column maps),
`tradingagents/dataflows/interface.py` (register in VENDOR_METHODS +
auto-route rule), `tests/test_akshare.py` (mocked-boundary tests).

Auto-routing extension: the existing `core_stock_apis`/`technical_indicators`
rule generalizes to `fundamental_data` and `news_data` categories when the
vendor config is exactly `yfinance` (default) and the symbol is A-share.
Explicit multi-vendor chains keep their configured semantics.

Look-ahead filter: report periods after `curr_date` dropped; disclosure-date
column preferred when present.

## Module 4: akshare-news

**Files**: `tradingagents/dataflows/akshare.py` (`get_news` adapter),
`tradingagents/dataflows/interface.py` (register), `tests/test_akshare.py`.

Window filter `start_date <= pub_date <= end_date`, cap at
`news_article_limit`, plain "No news found" on empty-in-window.

## Module 5: docs & regression sweep

**Files**: `README.md` (AKShare section: fundamentals + news now covered),
`RUNNING.md` (same), `CHANGELOG.md` (entry), full pytest + ruff run.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| akshare version column-name drift | column maps centralized; probe first (Step 0) |
| `.SS` routing change breaks a hidden expectation | full test suite gate; the only `.SS` tests found cover identity, not routing |
| statement endpoints keyed by period need N+1 calls (one per period) | cap periods to the last 8 (2 years); document the trade-off |
| Python 3.12 env vs CI 3.10-3.13 | 3.12 is inside CI matrix |
| East Money throttling on multi-period fetches | sequential fetches with small delay; tests mock the boundary |

## Historical scope (shipped, kept for reference)

The first vertical slice - OHLCV + technical indicators via
`tradingagents/dataflows/akshare.py`, opt-in vendor registration, and
`tests/test_akshare.py` - is complete and verified. The commands and success
criteria below remain the regression baseline for that slice.

---

## Objective (original slice)
Extend the `prophetss/TradingAgents` fork with an opt-in AKShare vendor for
Chinese A-share market data. Existing vendors and defaults remain unchanged.
The first vertical slice covers daily OHLCV and technical indicators, which
allows the Market Analyst to use AKShare without changing the agent contract.

## Tech Stack
- Python package managed by `pyproject.toml`
- AKShare as an optional runtime dependency
- Existing LangChain tools, vendor router, pandas, and stockstats
- pytest for tests

## Commands
- Install: `.venv/bin/python -m pip install '.[dev,akshare]'`
- Focused tests: `.venv/bin/python -m pytest tests/test_akshare.py`
- Full tests: `.venv/bin/python -m pytest`

## Project Structure
- `tradingagents/dataflows/akshare.py` — AKShare adapter and normalization.
- `tradingagents/dataflows/interface.py` — vendor registration and routing.
- `tradingagents/default_config.py` — opt-in vendor configuration.
- `tests/test_akshare.py` — isolated adapter and router tests.

## Code Style
Adapters expose the existing tool-compatible signatures and return the same
formatted text shape as the current stock vendor:

```python
def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    frame = _fetch_history(symbol, start_date, end_date)
    return _format_ohlcv(frame)
```

Use explicit validation and typed vendor errors; do not fabricate missing
market data or silently change the configured vendor chain.

## Testing Strategy
- Unit tests mock the external AKShare boundary and verify symbol/date
  conversion, OHLCV normalization, and router selection.
- Existing project tests must remain green.
- No live market API call is required for deterministic tests.

## Boundaries
- Always: Keep `yfinance` as the default; support Chinese A-share symbols
  (`600000`, `000001`, `300750`) and preserve existing tool signatures.
- Ask first: Expanding AKShare to futures, funds, news, macro, or financial
  statements; changing default vendors; adding live integration tests.
- Never: Commit API keys, alter upstream-only behavior, or fabricate data when
  AKShare returns no rows.

## Success Criteria
- The fork contains a selectable `akshare` vendor.
- `get_stock_data` and `get_indicators` can route to AKShare.
- AKShare is opt-in and existing default routing is unchanged.
- Missing dependency, invalid symbol, empty response, and malformed response
  fail explicitly.
- Focused and full test suites pass.

## Open Questions
- Broader AKShare coverage can be added later as a separate spec.
