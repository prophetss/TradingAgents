# Spec: ohlcv-fix - close_200_sma warm-up window

Module: `ohlcv-fix` (from `tasks/CAPABILITY-MAP.md`)

## Objective

`tradingagents/dataflows/akshare.py:110` computes the indicator fetch window as:

```python
start = (current - timedelta(days=max(look_back_days, 200) + 30)).strftime("%Y-%m-%d")
```

`200 + 30 = 230` **calendar** days contain only ~152 A-share **trading** days
(A-shares trade ~242 days/year), which is fewer than the 200 rows that
`rolling(200)` needs to warm up. Every value is NaN, `dropna()` empties the
frame, and `NoMarketDataError` is raised - so `close_200_sma` is *always*
`NO_DATA_AVAILABLE` on the akshare path for any `look_back_days < 200`.

Fix: request enough calendar days to cover `look_back_days` trading days plus
the indicator's warm-up rows. A-shares have ~242 trading days per 365 calendar
days; use a conversion factor of 365/242 ≈ 1.5 with margin, or simply fetch a
fixed 5-year window like `load_ohlcv` does (one cacheable shape).

## Approach (chosen)

Convert trading days to calendar days with margin:

```python
_TRADING_TO_CALENDAR = 365 / 242  # ~1.508

warmup_rows = _WARMUP_ROWS[indicator]  # e.g. 200 for close_200_sma, 50 for sma
needed_rows = warmup_rows + look_back_days + 10   # small safety margin
start = current - timedelta(days=ceil(needed_rows * _TRADING_TO_CALENDAR))
```

This keeps the request bounded (no 5-year fetch for a 10-day lookback of
`macd`) while guaranteeing enough rows.

## Success Criteria

1. `close_200_sma` with `look_back_days=30` on a synthetic 400-trading-day
   series returns non-empty CSV rows (not an exception).
2. `macd` with `look_back_days=10` still fetches a bounded window (<= ~120
   calendar days, not 5 years) - no regression to over-fetching.
3. A regression test pins the fix: it must fail on the old `+30` arithmetic.
4. All existing tests remain green.

## Commands

- Focused: `.venv/bin/python -m pytest tests/test_akshare.py -q`

## Boundaries

- Always: keep the 12 supported indicators and output format unchanged.
- Ask first: adding new indicators (e.g. `mfi`, which yfinance supports but
  akshare's path lacks - keep as a documented gap, do not silently diverge).
- Never: fabricating indicator values when rows are insufficient - raise
  `NoMarketDataError` with a precise `detail`.

## Open Questions

- None.
