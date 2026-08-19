"""AKShare data adapter for Chinese A-share daily market data."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta

import pandas as pd

from .errors import NoMarketDataError, VendorNotConfiguredError

_COLUMN_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "收盘": "Close",
    "最高": "High",
    "最低": "Low",
    "成交量": "Volume",
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


def _akshare_module():
    try:
        return importlib.import_module("akshare")
    except ImportError as exc:
        raise VendorNotConfiguredError(
            "AKShare is not installed. Install with: pip install 'tradingagents[akshare]'"
        ) from exc


def _normalize_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    for suffix in (".SH", ".SS", ".SZ"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    if len(value) != 6 or not value.isdigit():
        raise ValueError(
            f"AKShare requires a six-digit Chinese A-share symbol, got {symbol!r}"
        )
    return value


def _fetch_history(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    canonical = _normalize_symbol(symbol)
    start = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y%m%d")
    end = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y%m%d")
    frame = _akshare_module().stock_zh_a_hist(
        symbol=canonical,
        period="daily",
        start_date=start,
        end_date=end,
        adjust="qfq",
    )
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("AKShare stock_zh_a_hist must return a pandas DataFrame")
    if frame.empty:
        raise NoMarketDataError(symbol, canonical, f"no rows between {start_date} and {end_date}")
    missing = set(_COLUMN_MAP) - set(frame.columns)
    if missing:
        raise ValueError(f"AKShare response is missing columns: {sorted(missing)}")
    normalized = frame.rename(columns=_COLUMN_MAP)[list(_COLUMN_MAP.values())].copy()
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
    start = (current - timedelta(days=max(look_back_days, 200) + 30)).strftime("%Y-%m-%d")
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
