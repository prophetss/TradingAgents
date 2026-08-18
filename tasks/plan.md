# Spec: TradingAgents AKShare Data Vendor

## Objective
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
