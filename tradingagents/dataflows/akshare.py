"""AKShare data adapter for Chinese A-share daily market data."""

from __future__ import annotations

import importlib
import logging
import math
import time
from datetime import datetime, timedelta

import pandas as pd

from .errors import NoMarketDataError, VendorNotConfiguredError
from .symbol_utils import a_share_code, is_china_etf_symbol

logger = logging.getLogger(__name__)

# East Money's push2/push2his hosts intermittently drop client connections
# (RemoteDisconnected) - observed both from python-clients and curl, so it is
# a network/CDN behavior rather than a payload issue. A short retry ladder
# with backoff absorbs the transient drops; a mid-analysis tool call then
# degrades gracefully instead of crashing the whole run.
_FETCH_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 2.0

_COLUMN_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "收盘": "Close",
    "最高": "High",
    "最低": "Low",
    "成交量": "Volume",
}

# Sina fallback endpoint (stock_zh_a_daily) uses lowercase English columns.
_SINA_COLUMN_MAP = {
    "date": "Date",
    "open": "Open",
    "close": "Close",
    "high": "High",
    "low": "Low",
    "volume": "Volume",
}

_DESCRIPTIONS = {
    "close_50_sma": "50 SMA",
    "close_200_sma": "200 SMA",
    "close_10_ema": "10 EMA",
    "macd": "MACD",
    "macds": "MACD Signal",
    "macdh": "MACD Histogram",
    "rsi": "RSI",
    "boll": "Bollinger Middle",
    "boll_ub": "Bollinger Upper Band",
    "boll_lb": "Bollinger Lower Band",
    "atr": "ATR",
    "vwma": "VWMA",
}

# Rows of OHLCV history each indicator needs before its first non-NaN value
# (rolling windows in full; EMA chains warm up faster but are bounded by their
# longest span). Used to size the fetch window in calendar days.
_INDICATOR_WARMUP = {
    "close_50_sma": 50,
    "close_200_sma": 200,
    "close_10_ema": 10,
    "macd": 26,
    "macds": 35,   # EMA26 + signal EMA9
    "macdh": 35,
    "rsi": 15,
    "boll": 20,
    "boll_ub": 20,
    "boll_lb": 20,
    "atr": 15,     # true range needs 1 shift + 14 rolling
    "vwma": 20,
}


def _akshare_module():
    try:
        return importlib.import_module("akshare")
    except ImportError as exc:
        raise VendorNotConfiguredError(
            "AKShare is not installed. Install with: pip install 'tradingagents[akshare]'"
        ) from exc


def _normalize_symbol(symbol: str) -> str:
    """Return the bare six-digit A-share code, or raise for non-A-share input.

    Delegates to ``symbol_utils.a_share_code`` (the single source of truth)
    so the accepted suffix set (``.SH``/``.SS``/``.SZ`` and bare codes)
    matches what the routing layer recognizes.
    """
    code = a_share_code(symbol)
    if code is None:
        raise ValueError(
            f"AKShare requires a six-digit Chinese A-share symbol, got {symbol!r}"
        )
    return code


def _with_retries(description: str, func, *args, **kwargs):
    """Run an AKShare network call with a short retry ladder.

    East Money hosts drop connections transiently (RemoteDisconnected /
    ConnectionError); retrying a few times with backoff absorbs it. Genuine
    failures (no data, bad symbol) raise typed errors and are NOT retried -
    only connection-level exceptions match here. Note requests' ConnectionError
    does NOT subclass the builtin ConnectionError, so it is matched
    explicitly.
    """
    import requests as _requests

    retryable = (
        ConnectionError,          # builtin
        TimeoutError,             # builtin
        _requests.exceptions.ConnectionError,
        _requests.exceptions.Timeout,
    )
    last: Exception | None = None
    for attempt in range(_FETCH_RETRIES):
        try:
            return func(*args, **kwargs)
        except retryable as exc:
            last = exc
            logger.warning(
                "AKShare %s attempt %d/%d failed (%s); retrying in %.0fs",
                description, attempt + 1, _FETCH_RETRIES, exc, _RETRY_BACKOFF_SECONDS,
            )
            time.sleep(_RETRY_BACKOFF_SECONDS)
    raise last


def _sina_symbol(symbol: str) -> str:
    """Map an A-share ticker to Sina's prefix form (``sh600519``)."""
    code = _normalize_symbol(symbol)
    upper = symbol.strip().upper()
    if upper.endswith((".SH", ".SS")):
        return f"sh{code}"
    if upper.endswith(".SZ"):
        return f"sz{code}"
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _etf_symbol_from_code(code: str) -> str:
    """Rebuild a suffixed ETF symbol from a bare code by exchange range."""
    if code.startswith(("51", "52", "56", "58")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _fetch_etf_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch China ETF daily OHLCV: East Money ETF endpoint with Sina fallback.

    Mirrors the stock path's resilience: ``fund_etf_hist_em`` rides the same
    push2his CDN as stock history (which can go dark for a network path), so
    ``fund_etf_hist_sina`` serves as the fallback. The Sina ETF endpoint
    returns the full daily history with no date params - filtered locally -
    and uses the same lowercase English columns as the stock Sina fallback.
    """
    canonical = _normalize_symbol(symbol)
    start = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d")
    end = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d")
    try:
        frame = _with_retries(
            "fund_etf_hist_em",
            _akshare_module().fund_etf_hist_em,
            symbol=canonical,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="qfq",
        )
    except Exception as primary_error:
        logger.warning(
            "AKShare fund_etf_hist_em unusable (%s); trying Sina ETF fallback",
            primary_error,
        )
        frame = _with_retries(
            "fund_etf_hist_sina",
            _akshare_module().fund_etf_hist_sina,
            symbol=_sina_symbol(symbol),
        )
        if isinstance(frame, pd.DataFrame) and not frame.empty and "date" in frame.columns:
            dates = pd.to_datetime(frame["date"])
            frame = frame[
                (dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))
            ].copy()
    return frame


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    canonical = _normalize_symbol(symbol)
    # ETF classification needs the exchange suffix (or a code in ETF ranges);
    # the bare 6-digit code alone is ambiguous between exchanges, so classify
    # on the original symbol and fall back to code-range matching for bare
    # codes passed by internal callers (get_stock normalizes first).
    is_etf = is_china_etf_symbol(symbol) or (
        canonical.startswith(("51", "52", "56", "58", "15", "16"))
        and not canonical.startswith(("60", "68", "00", "30"))
    )
    if is_etf:
        frame = _fetch_etf_history(symbol if is_china_etf_symbol(symbol) else _etf_symbol_from_code(canonical), start_date, end_date)
        return _normalize_ohlcv(symbol, canonical, frame, start_date, end_date)
    start = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d")
    end = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d")
    try:
        frame = _with_retries(
            "stock_zh_a_hist",
            _akshare_module().stock_zh_a_hist,
            symbol=canonical,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="qfq",
        )
    except Exception as primary_error:
        # East Money's push2his CDN can go fully dark for a network path
        # (observed: direct AND proxied both 000 while other East Money
        # hosts stay 200). Fall back to the Sina daily endpoint - a different
        # provider/CDN serving the same qfq daily bars - before giving up.
        logger.warning(
            "AKShare stock_zh_a_hist unusable (%s); trying Sina fallback",
            primary_error,
        )
        frame = _with_retries(
            "stock_zh_a_daily",
            _akshare_module().stock_zh_a_daily,
            symbol=_sina_symbol(symbol),
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
    return _normalize_ohlcv(symbol, canonical, frame, start_date, end_date)


def _normalize_ohlcv(
    symbol: str, canonical: str, frame: pd.DataFrame, start_date: str, end_date: str
) -> pd.DataFrame:
    """Validate and normalize a raw OHLCV frame from any provider into the
    shared Date-indexed shape. Both providers' column sets are accepted
    (East Money Chinese headers, Sina lowercase English)."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("AKShare history fetch must return a pandas DataFrame")
    if frame.empty:
        raise NoMarketDataError(symbol, canonical, f"no rows between {start_date} and {end_date}")
    column_map = _COLUMN_MAP if "日期" in frame.columns else _SINA_COLUMN_MAP
    missing = set(column_map) - set(frame.columns)
    if missing:
        raise ValueError(f"AKShare response is missing columns: {sorted(missing)}")
    normalized = frame.rename(columns=column_map)[list(column_map.values())].copy()
    normalized["Date"] = pd.to_datetime(normalized["Date"], errors="raise")
    for column in normalized.columns[1:]:
        normalized[column] = pd.to_numeric(normalized[column], errors="raise")
    normalized = normalized.sort_values("Date").set_index("Date")
    return normalized.loc[start_date:end_date]


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return AKShare daily OHLCV data in TradingAgents' stock-tool format."""
    canonical = _normalize_symbol(symbol)
    frame = _fetch_history(canonical, start_date, end_date)
    if frame.empty:
        raise NoMarketDataError(symbol, canonical, f"no rows between {start_date} and {end_date}")
    output = frame.copy()
    output.index.name = "Date"
    output = output.round(2)
    header = (
        f"# Stock data for {canonical} from {start_date} to {end_date}\n"
        f"# Total records: {len(output)}\n"
        f"# Data source: AKShare (qfq)\n\n"
    )
    return header + output.to_csv()


def get_indicator(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
) -> str:
    """Calculate the standard TradingAgents indicators from AKShare OHLCV data."""
    if indicator not in _DESCRIPTIONS:
        raise ValueError(f"Indicator {indicator} is not supported by AKShare")
    current = datetime.strptime(curr_date, "%Y-%m-%d")
    # Trading-day-aware warm-up: A-shares trade ~242 days per 365-calendar-day
    # year, so a window sized in calendar days must be scaled up or slow
    # indicators (200 SMA needs 200 trading rows) come back all-NaN and the
    # caller sees NO_DATA forever. The old `+ 30 calendar days` arithmetic
    # covered only ~152 trading rows - always short of 200.
    warmup = _INDICATOR_WARMUP.get(indicator, 200)
    needed_rows = warmup + max(look_back_days, 1) + 10  # safety margin
    calendar_days = math.ceil(needed_rows * 365 / 242)
    start = (current - timedelta(days=calendar_days)).strftime("%Y-%m-%d")
    frame = _fetch_history(symbol, start, curr_date)
    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"]
    values = pd.DataFrame(index=frame.index)
    values["close_50_sma"] = close.rolling(50).mean()
    values["close_200_sma"] = close.rolling(200).mean()
    values["close_10_ema"] = close.ewm(span=10, adjust=False).mean()
    values["macd"] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    values["macds"] = values["macd"].ewm(span=9, adjust=False).mean()
    values["macdh"] = values["macd"] - values["macds"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    values["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, pd.NA)))
    values["boll"] = close.rolling(20).mean()
    deviation = close.rolling(20).std()
    values["boll_ub"] = values["boll"] + 2 * deviation
    values["boll_lb"] = values["boll"] - 2 * deviation
    true_range = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    values["atr"] = true_range.rolling(14).mean()
    values["vwma"] = (close * volume).rolling(20).sum() / volume.rolling(20).sum()
    result = values[[indicator]].dropna().tail(look_back_days).round(4)
    if result.empty:
        raise NoMarketDataError(symbol, _normalize_symbol(symbol), "not enough rows for indicator")
    return f"# {_DESCRIPTIONS[indicator]} for {symbol} as of {curr_date}\n\n{result.to_csv()}"


# --- Fundamentals (East Money statement endpoints) ---------------------------
#
# akshare 1.18.x exposes the three statements per-symbol with the
# ``SH600519``/``SZ000001`` prefix form; rows come back newest-first with
# ``REPORT_DATE`` (报告期) and ``NOTICE_DATE`` (披露日期) columns. Values are
# CNY yuan. Column names verified live against 1.18.92 (see tasks/todo.md).

# English display labels for the report rows exposed to the agents. Kept to a
# curated set the fundamental analysts actually reason over - the raw payloads
# carry 100+ columns of statutory detail that mostly adds token noise.
_STATEMENT_COLUMNS = {
    "balance": [
        ("MONETARYFUNDS", "Cash & Equivalents"),
        ("ACCOUNTS_RECE", "Accounts Receivable"),
        ("INVENTORY", "Inventory"),
        ("TOTAL_ASSETS", "Total Assets"),
        ("TOTAL_LIABILITIES", "Total Liabilities"),
        ("TOTAL_EQUITY", "Total Equity"),
        ("MINORITY_EQUITY", "Minority Interest"),
        ("TOTAL_PARENT_EQUITY", "Shareholders' Equity"),
    ],
    "profit": [
        ("TOTAL_OPERATE_INCOME", "Total Revenue"),
        ("OPERATE_INCOME", "Operating Revenue"),
        ("OPERATE_COST", "Operating Cost"),
        ("OPERATE_PROFIT", "Operating Profit"),
        ("TOTAL_PROFIT", "Total Profit"),
        ("NETPROFIT", "Net Profit"),
        ("PARENT_NETPROFIT", "Net Profit (Parent)"),
        ("BASIC_EPS", "Basic EPS"),
        ("DILUTED_EPS", "Diluted EPS"),
    ],
    "cashflow": [
        ("NETCASH_OPERATE", "Operating Cash Flow"),
        ("NETCASH_INVEST", "Investing Cash Flow"),
        ("NETCASH_FINANCE", "Financing Cash Flow"),
        ("END_CCE", "End Cash & Equivalents"),
    ],
}

_STATEMENT_FETCHERS = {
    "balance": "stock_balance_sheet_by_report_em",
    "profit": "stock_profit_sheet_by_report_em",
    "cashflow": "stock_cash_flow_sheet_by_report_em",
}


def _em_symbol(symbol: str) -> str:
    """Map an A-share ticker to East Money's prefix form (``SH600519``).

    The exchange prefix follows the code's listing range: 6xxxxx Shanghai,
    0xxxxx/3xxxxx Shenzhen - matching how the suffixes were written by the
    user when one is present.
    """
    code = _normalize_symbol(symbol)
    upper = symbol.strip().upper()
    if upper.endswith((".SH", ".SS")):
        return f"SH{code}"
    if upper.endswith(".SZ"):
        return f"SZ{code}"
    if code.startswith("6"):
        return f"SH{code}"
    return f"SZ{code}"


def _fetch_statement(kind: str, em: str, code: str) -> pd.DataFrame:
    """Fetch one of the three statements for the East Money symbol ``em``
    (``SH600519`` form), newest-first, validated. ``code`` is the bare
    six-digit form used in error messages."""
    fetcher = getattr(_akshare_module(), _STATEMENT_FETCHERS[kind])
    frame = _with_retries(f"{kind} statement", fetcher, symbol=em)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise NoMarketDataError(em, code, f"no {kind} statement rows returned")
    return frame


def _filter_by_disclosure(frame: pd.DataFrame, curr_date: str | None, annual_only: bool) -> pd.DataFrame:
    """Apply the look-ahead contract: a row is visible only if its disclosure
    date (falling back to report date + statutory max lag) is on/before
    ``curr_date``. Annual-only keeps Dec-31 periods."""
    if "NOTICE_DATE" in frame.columns:
        notice = pd.to_datetime(frame["NOTICE_DATE"], errors="coerce")
    else:
        notice = pd.Series(pd.NaT, index=frame.index)
    report = pd.to_datetime(frame.get("REPORT_DATE"), errors="coerce")

    if curr_date:
        curr = pd.Timestamp(curr_date)
        # Disclosure date when present; else assume the statutory worst case
        # (a period is published at most ~4 months after period end - annual
        # reports by Apr 30). This keeps backtests from seeing reports that
        # had not been published yet at the requested date.
        fallback = report + pd.Timedelta(days=121)
        visible = notice.fillna(fallback) <= curr
        frame = frame[visible]

    if annual_only:
        frame = frame[report.dt.month == 12]

    return frame.sort_values("REPORT_DATE", ascending=False)


def _format_statement(kind: str, title: str, ticker: str, freq: str, frame: pd.DataFrame) -> str:
    columns = _STATEMENT_COLUMNS[kind]
    present = [(raw, label) for raw, label in columns if raw in frame.columns]
    report = frame.head(8)  # up to 2 years of periods - the agents' horizon
    rows = []
    for _, record in report.iterrows():
        period = str(record.get("REPORT_DATE", ""))[:10]
        for raw, label in present:
            value = record.get(raw)
            if pd.notna(value):
                rows.append({"Item": label, "Period": period, "Value (CNY)": value})
    if not rows:
        raise NoMarketDataError(
            ticker, _normalize_symbol(ticker), f"{kind} statement had no usable rows"
        )
    table = pd.DataFrame(rows).pivot_table(
        index="Item", columns="Period", values="Value (CNY)", aggfunc="first"
    )
    header = (
        f"# {title} for {_normalize_symbol(ticker)} ({freq})\n"
        f"# Currency: CNY (yuan). Data source: AKShare (East Money).\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + table.round(2).to_csv()


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Company overview assembled from East Money statement payloads.

    ``stock_individual_info_em`` (the natural profile endpoint) is only used as
    best-effort enrichment - it rides the push2 host, which is intermittently
    unreachable, so the overview must stand on the statement endpoints that
    ride the stable datacenter host.
    """
    code = _normalize_symbol(ticker)
    em = _em_symbol(ticker)

    overview_lines: list[str] = []
    try:
        info = _akshare_module().stock_individual_info_em(symbol=code)
        if isinstance(info, pd.DataFrame) and not info.empty:
            mapping = dict(zip(info.get("item", []), info.get("value", []), strict=False))
            label_map = {
                "股票简称": "Name",
                "行业": "Industry",
                "总市值": "Market Cap (CNY)",
                "流通市值": "Float Market Cap (CNY)",
                "总股本": "Total Shares",
                "流通股": "Float Shares",
            }
            for zh, en in label_map.items():
                if zh in mapping and pd.notna(mapping[zh]):
                    overview_lines.append(f"{en}: {mapping[zh]}")
    except Exception:  # noqa: BLE001 - enrichment is optional by design
        pass

    balance = _filter_by_disclosure(
        _fetch_statement("balance", em, code), curr_date, annual_only=False
    )
    profit = _filter_by_disclosure(
        _fetch_statement("profit", em, code), curr_date, annual_only=False
    )
    if balance.empty or profit.empty:
        raise NoMarketDataError(ticker, code, "no fundamental rows returned")

    latest_profit = profit.iloc[0]
    latest_balance = balance.iloc[0]
    lines = list(overview_lines)
    for raw, label in _STATEMENT_COLUMNS["profit"]:
        value = latest_profit.get(raw)
        if pd.notna(value) and raw in latest_profit.index:
            lines.append(f"{label} (latest, {str(latest_profit.get('REPORT_DATE', ''))[:10]}): {value:,.2f}")
    for raw, label in _STATEMENT_COLUMNS["balance"]:
        value = latest_balance.get(raw)
        if pd.notna(value) and raw in latest_balance.index:
            lines.append(f"{label} (latest, {str(latest_balance.get('REPORT_DATE', ''))[:10]}): {value:,.2f}")

    header = (
        f"# Company Fundamentals for {code}\n"
        f"# Currency: CNY (yuan). Data source: AKShare (East Money).\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + "\n".join(lines)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    code = _normalize_symbol(ticker)
    frame = _filter_by_disclosure(
        _fetch_statement("balance", _em_symbol(ticker), code),
        curr_date,
        annual_only=freq.lower() == "annual",
    )
    return _format_statement("balance", "Balance Sheet data", ticker, freq, frame)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    code = _normalize_symbol(ticker)
    frame = _filter_by_disclosure(
        _fetch_statement("cashflow", _em_symbol(ticker), code),
        curr_date,
        annual_only=freq.lower() == "annual",
    )
    return _format_statement("cashflow", "Cash Flow data", ticker, freq, frame)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    code = _normalize_symbol(ticker)
    frame = _filter_by_disclosure(
        _fetch_statement("profit", _em_symbol(ticker), code),
        curr_date,
        annual_only=freq.lower() == "annual",
    )
    return _format_statement("profit", "Income Statement data", ticker, freq, frame)


# --- News (East Money per-stock feed) ----------------------------------------

_NEWS_COLUMNS = {
    "title": "新闻标题",
    "content": "新闻内容",
    "published": "发布时间",
    "source": "文章来源",
    "url": "新闻链接",
}


def get_news(symbol: str, start_date: str, end_date: str) -> str:
    """Per-stock news from East Money, filtered to [start_date, end_date].

    ``stock_news_em`` serves the *recent* feed; a historical window far in the
    past legitimately returns nothing, which is reported as plain text the
    same way the yfinance path does (not an error).
    """
    from .config import get_config

    code = _normalize_symbol(symbol)
    frame = _with_retries(
        "stock_news_em", _akshare_module().stock_news_em, symbol=code
    )
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise NoMarketDataError(symbol, code, "no news rows returned")

    limit = get_config().get("news_article_limit", 20)
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)  # inclusive end

    published = pd.to_datetime(frame[_NEWS_COLUMNS["published"]], errors="coerce")
    in_window = frame[(published >= start) & (published < end)]
    if in_window.empty:
        return f"No news found for {symbol} between {start_date} and {end_date}"

    parts = []
    for _, article in in_window.head(limit).iterrows():
        title = article.get(_NEWS_COLUMNS["title"], "")
        source = article.get(_NEWS_COLUMNS["source"], "East Money")
        content = str(article.get(_NEWS_COLUMNS["content"], "") or "")[:400]
        url = article.get(_NEWS_COLUMNS["url"], "")
        entry = f"### {title} (source: {source})\n"
        if content:
            entry += f"{content}\n"
        if url:
            entry += f"Link: {url}\n"
        parts.append(entry + "\n")

    return f"## {symbol} News, from {start_date} to {end_date}:\n\n{''.join(parts)}"
