# Spec: akshare-news - East Money per-stock news

Module: `akshare-news` (from `tasks/CAPABILITY-MAP.md`)

## Objective

Wire AKShare's `stock_news_em(symbol)` (East Money per-stock news feed,
keyless) into the `get_news` tool so A-share symbols get real news instead of
yfinance's empty result. The news analyst currently receives nothing for
`.SH`/`.SZ`/`.SS` symbols.

## Interface contract

```python
def get_news(symbol: str, start_date: str, end_date: str) -> str
```

(matches `get_news_yfinance`; registered in `VENDOR_METHODS["get_news"]`
under `akshare`; auto-routing rule identical to `akshare-fundamentals`).

## Date-window filtering (look-ahead safe)

`stock_news_em` returns recent articles with a publication timestamp column.
The adapter:

1. Parses the publish date (column name verified against the installed
   akshare version; recorded in the implementation notes).
2. Keeps only articles with `start_date <= pub_date <= end_date` - same
   window semantics as `_in_news_window` in `yfinance_news.py`.
3. Caps the output at `config["news_article_limit"]` articles (same budget
   as the yfinance path).
4. Returns `"No news found for <symbol> between <start> and <end>"` when the
   filter empties the feed (matching yfinance's wording) - not an error,
   because "no coverage in window" is a legitimate outcome.

One caveat documented for the agent: `stock_news_em` serves *recent* news;
a historical window far in the past may legitimately return nothing. The
header includes the fetched window so the agent can reason about it.

## Output shape

Same as yfinance path:

```
## 600519.SH News, from 2026-08-01 to 2026-08-19:

### <title> (source: <source>)
<content snippet>
Link: <url>

```

## Success Criteria

1. `route_to_vendor("get_news", "600519.SH", "2026-08-01", "2026-08-19")`
   under default `yfinance` config returns akshare-formatted articles
   (mocked boundary), with no article outside the window.
2. Non-A-share symbols never route to the akshare news adapter.
3. Empty-in-window returns the plain "No news found" string, not an error.
4. Missing dependency raises `VendorNotConfiguredError`.
5. All existing tests remain green.

## Commands

- Focused: `.venv/bin/python -m pytest tests/test_akshare.py -q`

## Boundaries

- Always: mock the akshare boundary in tests; respect
  `news_article_limit`.
- Ask first: global/macro news (a different endpoint family), announcement
  filings (`stock_notice_report`), research reports.
- Never: fabricating articles or dates; leaking full article bodies when the
  endpoint returns long content (snippet only, like yfinance's summary).

## Open Questions

- Exact publish-date column name for the installed akshare version -
  resolved by live probing before implementation.
