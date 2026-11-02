# Spec: symbol-unify — A-share symbol single source of truth

Module: `symbol-unify` (from `tasks/CAPABILITY-MAP.md`)

## Objective

Today, A-share symbol knowledge is scattered across three inconsistent
implementations:

- `tradingagents/dataflows/interface.py:37` `_is_a_share_symbol` — `.SH/.SZ` only
- `tradingagents/dataflows/stockstats_utils.py:29` `_is_a_share_symbol` — `.SH/.SZ` only (verbatim duplicate)
- `tradingagents/dataflows/akshare.py:48` `_normalize_symbol` — `.SH/.SS/.SZ`
- `tradingagents/default_config.py:152` `benchmark_map` — `.SS/.SZ` only

Consequences (verified): a user following the README's market table and typing
`600519.SS` silently gets yfinance (no data) instead of akshare; a user typing
`600519.SH` gets akshare prices but a permanently-pending memory-log entry and
a nonsense SPY benchmark in the reflection layer.

Consolidate detection and normalization into `tradingagents/dataflows/symbol_utils.py`
as the single source of truth, and make the reflection layer and benchmark map
A-share-aware.

## Success Criteria

1. `symbol_utils.py` exposes `is_a_share_symbol(symbol) -> bool` and
   `a_share_code(symbol) -> str` (6-digit code) — accepting `.SH`, `.SS`,
   `.SZ`, and bare 6-digit codes.
2. `interface.py`, `stockstats_utils.py`, and `akshare.py` all delegate to
   these functions; the two duplicated `_is_a_share_symbol` bodies are deleted.
3. `benchmark_map` gains a `.SH` entry mapping to the SSE composite; `_resolve_benchmark`
   resolves `600519.SH` -> `000001.SS` (Yahoo-symbol form for the reflection
   layer's yfinance lookup).
4. `_fetch_returns` resolves A-share tickers to their Yahoo equivalent
   (`.SH` -> `.SS`) so realized-return lookups succeed and memory-log entries
   stop pending forever.
5. All existing tests remain green; new unit tests cover the unification
   (`.SS` now routes to akshare like `.SH`).

## Commands

- Focused: `.venv/bin/python -m pytest tests/test_symbol_utils.py tests/test_akshare.py -q`
- Full: `.venv/bin/python -m pytest -q`

## Boundaries

- Always: keep `normalize_symbol`'s existing Yahoo behavior for non-A-share
  symbols byte-identical; pure syntactic functions only (no network).
- Ask first: renaming any public symbol; touching `_ALIASES`.
- Never: making akshare the default vendor for non-A-share symbols.

## Open Questions

- None — resolution order for mixed forms (e.g. `600519.ss` lowercase) is
  case-insensitive everywhere, matching existing `.strip().upper()` behavior.
