from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.router import route_to_vendor
from tradingagents.dataflows.symbols import (
    a_share_code,
    a_share_yahoo_symbol,
    is_a_share_symbol,
)
from tradingagents.dataflows.vendors import akshare
from tradingagents.dataflows.vendors.yahoo import ohlcv as stockstats_utils


def _history_frame():
    dates = pd.date_range("2026-01-01", periods=240, freq="D")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": range(100, 340),
            "收盘": range(101, 341),
            "最高": range(102, 342),
            "最低": range(99, 339),
            "成交量": range(1000, 1240),
            "成交额": range(10000, 10240),
        }
    )


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, _isolate_config):
    """Keep each test's OHLCV cache to itself: the vendor now shares it."""
    set_config({"data_cache_dir": str(tmp_path)})


@pytest.fixture()
def fake_akshare(monkeypatch):
    fake = SimpleNamespace(
        stock_zh_a_hist=lambda **kwargs: _history_frame(),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    return fake


@pytest.mark.unit
def test_akshare_normalizes_a_share_history(fake_akshare):
    result = akshare.get_stock("600000.SS", "2026-01-01", "2026-01-10")

    assert "# Stock data for 600000" in result
    assert "Date,Open,Close,High,Low,Volume" in result
    assert "2026-01-01" in result


@pytest.mark.unit
def test_akshare_routes_stock_data_when_selected(fake_akshare):
    set_config({"data_vendors": {"core_stock_apis": "akshare"}})

    result = route_to_vendor("get_stock_data", "000001", "2026-01-01", "2026-01-10")

    assert "# Stock data for 000001" in result


@pytest.mark.unit
def test_akshare_rejects_non_a_share_symbols(fake_akshare):
    with pytest.raises(ValueError, match="six-digit Chinese A-share"):
        akshare.get_stock("AAPL", "2026-01-01", "2026-01-10")


@pytest.mark.unit
def test_sh_suffix_automatically_routes_to_akshare(fake_akshare):
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})

    with mock.patch.dict(
        __import__("tradingagents.dataflows.router", fromlist=["VENDOR_METHODS"]).VENDOR_METHODS,
        {"get_stock_data": {"akshare": akshare.get_stock, "yfinance": lambda *args: "YFINANCE_DATA"}},
        clear=False,
    ):
        result = route_to_vendor("get_stock_data", "600000.SH", "2026-01-01", "2026-01-10")

    assert "# Stock data for 600000" in result


@pytest.mark.unit
def test_sz_suffix_automatically_routes_to_akshare(fake_akshare):
    set_config({"data_vendors": {"technical_indicators": "yfinance"}})

    with mock.patch.dict(
        __import__("tradingagents.dataflows.router", fromlist=["VENDOR_METHODS"]).VENDOR_METHODS,
        {
            "get_indicators": {
                "akshare": akshare.get_indicator,
                "yfinance": lambda *args: "YFINANCE_DATA",
            }
        },
        clear=False,
    ):
        result = route_to_vendor(
            "get_indicators", "000001.SZ", "macd", "2026-01-10", 10
        )

    assert "MACD" in result


@pytest.mark.unit
def test_non_a_share_automatically_uses_yfinance(monkeypatch):
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
    import tradingagents.dataflows.router as interface
    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *args: "YFINANCE_DATA")

    result = route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")

    assert result == "YFINANCE_DATA"


@pytest.mark.unit
def test_load_ohlcv_uses_akshare_for_a_share(monkeypatch, tmp_path):
    set_config({"data_cache_dir": str(tmp_path)})
    today = pd.Timestamp("2026-01-10")
    monkeypatch.setattr(stockstats_utils.pd.Timestamp, "today", staticmethod(lambda: today))
    monkeypatch.setattr(
        akshare,
        "_fetch_history",
        lambda *args: pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1000, 1100],
            },
            index=pd.to_datetime(["2026-01-09", "2026-01-10"]),
        ).rename_axis("Date"),
    )
    monkeypatch.setattr(
        stockstats_utils.yf,
        "download",
        lambda *args, **kwargs: pytest.fail("Yahoo Finance must not be called for A-shares"),
    )

    result = stockstats_utils.load_ohlcv("600519.SH", "2026-01-10")

    assert result["Close"].tolist() == [101.0, 102.0]


# --- symbol-unify -----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("600519.SH", True),
        ("600519.SS", True),   # Yahoo form must also route to akshare now
        ("000001.SZ", True),
        ("600519.sh", True),   # case-insensitive
        ("AAPL", False),
        ("0700.HK", False),
        ("60051", False),      # too short
        ("600519.XX", False),  # unknown suffix
        ("", False),
        (None, False),
    ],
)
def test_is_a_share_symbol_unified(symbol, expected):
    assert is_a_share_symbol(symbol) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("600519.SH", "600519"),
        ("600519.SS", "600519"),
        ("000001.SZ", "000001"),
        ("600519", "600519"),  # bare code accepted for explicit akshare use
        ("AAPL", None),
        ("0700.HK", None),
    ],
)
def test_a_share_code_extraction(symbol, expected):
    assert a_share_code(symbol) == expected


@pytest.mark.unit
def test_a_share_yahoo_symbol_maps_sh_to_ss():
    # The reflection layer prices A-shares via Yahoo, which uses .SS.
    assert a_share_yahoo_symbol("600519.SH") == "600519.SS"
    assert a_share_yahoo_symbol("600519.SS") == "600519.SS"
    assert a_share_yahoo_symbol("000001.SZ") == "000001.SZ"
    assert a_share_yahoo_symbol("AAPL") is None


@pytest.mark.unit
def test_ss_suffix_routes_to_akshare_for_ohlcv(fake_akshare):
    """Regression: .SS (the README's documented Shanghai suffix) used to fall
    through to yfinance, which has no data for it - now it routes to akshare
    like .SH does."""
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})

    with mock.patch.dict(
        __import__("tradingagents.dataflows.router", fromlist=["VENDOR_METHODS"]).VENDOR_METHODS,
        {"get_stock_data": {"akshare": akshare.get_stock, "yfinance": lambda *args: "YFINANCE_DATA"}},
        clear=False,
    ):
        result = route_to_vendor("get_stock_data", "600519.SS", "2026-01-01", "2026-01-10")

    assert "# Stock data for 600519" in result


@pytest.mark.unit
def test_benchmark_map_covers_broker_sh_suffix():
    from tradingagents.default_config import DEFAULT_CONFIG

    benchmark_map = DEFAULT_CONFIG["benchmark_map"]
    assert benchmark_map[".SH"] == "000001.SS"
    assert benchmark_map[".SS"] == "000001.SS"


@pytest.mark.unit
def test_fetch_returns_resolves_sh_to_yahoo_ss(monkeypatch):
    """Regression: A-share realized returns were priced with the broker ``.SH``
    suffix, which Yahoo does not know - the memory-log entry then stayed pending
    forever. The reflection layer's price lookup now normalizes to ``.SS``."""
    from tradingagents.dataflows.vendors.yahoo import market

    calls = []

    class _FakeTicker:
        def __init__(self, symbol):
            calls.append(symbol)

        def history(self, start, end):
            return pd.DataFrame(
                {"Close": [100.0, 110.0]},
                index=pd.to_datetime(["2026-01-15", "2026-01-20"]),
            )

    monkeypatch.setattr(market.yf, "Ticker", _FakeTicker)

    closes = market.get_closes("600519.SH", "2026-01-15", "2026-01-20")

    assert calls[0] == "600519.SS"  # resolved, not the raw .SH
    assert closes.tolist() == [100.0, 110.0]


# --- ohlcv-fix --------------------------------------------------------------


@pytest.mark.unit
def test_close_200_sma_works_from_the_shared_history(monkeypatch):
    """Regression: the old calendar-day window (max(lookback, 200) + 30 days)
    contained only ~152 A-share trading days, so rolling(200) produced all-NaN
    and close_200_sma raised NoMarketDataError for every normal lookback. The
    indicator now reads the 5-year daily cache, which is ample warm-up."""
    requested = []

    def fake_fetch(symbol, start_date, end_date):
        requested.append((start_date, end_date))
        # Simulate real trading-day density: ~242 rows per 365 days.
        days = pd.date_range(start_date, end_date, freq="D")
        idx = days[days.dayofweek < 5]  # weekdays as density proxy
        n = len(idx)
        return pd.DataFrame(
            {
                "Open": range(100, 100 + n),
                "High": range(102, 102 + n),
                "Low": range(99, 99 + n),
                "Close": range(101, 101 + n),
                "Volume": range(1000, 1000 + n),
            },
            index=idx,
        ).rename_axis("Date")

    monkeypatch.setattr(akshare, "_fetch_history", fake_fetch)

    result = akshare.get_indicator("600519.SH", "close_200_sma", "2026-01-10", 30)

    # The cache spans five years, comfortably more than the 200 trading rows
    # the indicator needs.
    start_str, _ = requested[0]
    span_days = (datetime.strptime("2026-01-10", "%Y-%m-%d") - datetime.strptime(start_str, "%Y-%m-%d")).days
    assert span_days >= 365 * 4
    # And the indicator actually returns rows instead of raising.
    assert "200 SMA" in result
    assert result.count("\n") >= 2  # header + at least one data row


@pytest.mark.unit
def test_indicators_share_one_fetch(monkeypatch):
    """The snapshot, every indicator and the stock tool read one per-symbol
    cache, so a run asks East Money for the history once, not per indicator."""
    calls = []

    def fake_fetch(symbol, start_date, end_date):
        calls.append(symbol)
        days = pd.date_range(start_date, end_date, freq="D")
        idx = days[days.dayofweek < 5]
        n = len(idx)
        return pd.DataFrame(
            {
                "Open": range(100, 100 + n),
                "High": range(102, 102 + n),
                "Low": range(99, 99 + n),
                "Close": range(101, 101 + n),
                "Volume": range(1000, 1000 + n),
            },
            index=idx,
        ).rename_axis("Date")

    monkeypatch.setattr(akshare, "_fetch_history", fake_fetch)

    akshare.get_indicator("600519.SH", "macd", "2026-01-10", 10)
    akshare.get_indicator("600519.SH", "boll", "2026-01-10", 10)
    akshare.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    assert len(calls) == 1


# --- akshare-fundamentals ----------------------------------------------------


def _statement_frame(kind: str) -> pd.DataFrame:
    """Synthetic East Money statement payload shaped like the live one."""
    base = {
        "SECUCODE": "600519.SH",
        "SECURITY_CODE": "600519",
        "REPORT_DATE": ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"],
        "REPORT_TYPE": ["中报", "一季报", "年报", "三季报"],
        "NOTICE_DATE": ["2026-08-15", "2026-04-25", "2026-04-17", "2025-10-28"],
    }
    values = {
        "balance": {
            "MONETARYFUNDS": [1e10, 9e9, 8e9, 7e9],
            "TOTAL_ASSETS": [2.7e11, 2.6e11, 2.5e11, 2.4e11],
            "TOTAL_LIABILITIES": [4e10, 3.9e10, 3.8e10, 3.7e10],
            "TOTAL_EQUITY": [2.3e11, 2.2e11, 2.1e11, 2.0e11],
        },
        "profit": {
            "TOTAL_OPERATE_INCOME": [8e10, 4e10, 1.7e11, 1.2e11],
            "NETPROFIT": [4e10, 2e10, 8e10, 6e10],
            "BASIC_EPS": [31.0, 15.5, 64.0, 48.0],
        },
        "cashflow": {
            "NETCASH_OPERATE": [3e10, 1.5e10, 6e10, 4e10],
            "END_CCE": [1e10, 9e9, 8e9, 7e9],
        },
    }[kind]
    frame = pd.DataFrame(base)
    for column, series in values.items():
        frame[column] = series
    return frame


@pytest.fixture()
def fake_akshare_statements(monkeypatch):
    calls = []

    def _fetcher(symbol):
        calls.append(symbol)
        # Dispatch by endpoint name via the module attribute being called.
        name = _fetcher._current
        return _statement_frame(name)

    fake = SimpleNamespace(
        stock_balance_sheet_by_report_em=lambda symbol: _fetch_with(name="balance", symbol=symbol, calls=calls, frame=_statement_frame("balance")),
        stock_profit_sheet_by_report_em=lambda symbol: _fetch_with(name="profit", symbol=symbol, calls=calls, frame=_statement_frame("profit")),
        stock_cash_flow_sheet_by_report_em=lambda symbol: _fetch_with(name="cashflow", symbol=symbol, calls=calls, frame=_statement_frame("cashflow")),
        stock_individual_info_em=lambda symbol: pd.DataFrame(
            {"item": ["股票简称", "行业", "总市值"], "value": ["贵州茅台", "白酒", 2.0e12]}
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    return fake


def _fetch_with(name, symbol, calls, frame):
    calls.append((name, symbol))
    return frame.copy()


@pytest.mark.unit
def test_fundamentals_assembles_overview(fake_akshare_statements):
    result = akshare.get_fundamentals("600519.SH", "2026-08-19")

    assert "# Company Fundamentals for 600519" in result
    assert "贵州茅台" in result           # from the profile enrichment
    assert "Total Revenue" in result       # from the profit statement
    assert "Total Assets" in result        # from the balance statement
    assert "CNY" in result                 # currency is explicit


@pytest.mark.unit
def test_balance_sheet_respects_freq_and_lookahead(fake_akshare_statements):
    quarterly = akshare.get_balance_sheet("600519.SH", freq="quarterly", curr_date="2026-08-19")
    assert "# Balance Sheet data for 600519 (quarterly)" in quarterly
    assert quarterly.count("2026-06-30") >= 1  # disclosed Aug-15, visible on Aug-19

    # Annual keeps only Dec-31 periods.
    annual = akshare.get_balance_sheet("600519.SH", freq="annual", curr_date="2026-08-19")
    assert "2025-12-31" in annual
    assert "2026-06-30" not in annual


@pytest.mark.unit
def test_statement_lookahead_filters_future_disclosures(fake_akshare_statements):
    # On 2026-05-01 the H1-2026 report (disclosed 2026-08-15) must be hidden.
    result = akshare.get_income_statement("600519.SH", freq="quarterly", curr_date="2026-05-01")
    assert "2026-06-30" not in result   # period not yet disclosed
    assert "2026-03-31" in result       # disclosed 2026-04-25, visible


@pytest.mark.unit
def test_a_share_fundamentals_auto_route(fake_akshare_statements):
    """Default yfinance config + .SH symbol -> akshare serves the call (the
    2026-08-19 real run had all four tools NO_DATA under this exact setup)."""
    set_config({"data_vendors": {"fundamental_data": "yfinance"}})

    result = route_to_vendor("get_fundamentals", "600519.SH", "2026-08-19")
    assert "# Company Fundamentals for 600519" in result


@pytest.mark.unit
def test_non_a_share_fundamentals_never_route_to_akshare(fake_akshare_statements, monkeypatch):
    set_config({"data_vendors": {"fundamental_data": "yfinance"}})
    import tradingagents.dataflows.router as interface

    monkeypatch.setattr(
        akshare, "get_fundamentals",
        lambda *args: pytest.fail("akshare must not serve non-A-share symbols"),
    )
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_fundamentals"],
        "akshare",
        akshare.get_fundamentals,
    )

    with mock.patch("tradingagents.dataflows.vendors.yahoo.fundamentals.get_fundamentals") as fake_yf:
        fake_yf.return_value = "YFINANCE_FUNDAMENTALS"
        # yfinance raises through the router's generic-exception path; patch at
        # the VENDOR_METHODS layer instead for isolation.
        monkeypatch.setitem(
            interface.VENDOR_METHODS["get_fundamentals"], "yfinance", fake_yf
        )
        result = route_to_vendor("get_fundamentals", "AAPL", "2026-08-19")

    assert result == "YFINANCE_FUNDAMENTALS"


# --- akshare-news ------------------------------------------------------------


@pytest.fixture()
def fake_akshare_news(monkeypatch):
    fake = SimpleNamespace(
        stock_news_em=lambda symbol: pd.DataFrame(
            {
                "关键词": ["600519"] * 4,
                "新闻标题": ["新闻A", "新闻B", "新闻C", "新闻D"],
                "新闻内容": ["内容A", "内容B", "内容C", "内容D"],
                "发布时间": [
                    "2026-08-18 10:00:00",
                    "2026-08-10 09:00:00",
                    "2026-07-01 08:00:00",  # outside window
                    "2026-08-19 23:00:00",  # outside window (end is inclusive day)
                ],
                "文章来源": ["东财", "证券时报", "新浪", "同花顺"],
                "新闻链接": [f"http://n{i}.example" for i in range(4)],
            }
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    return fake


@pytest.mark.unit
def test_news_window_filtering(fake_akshare_news):
    set_config({"news_article_limit": 20})
    result = akshare.get_news("600519.SH", "2026-08-01", "2026-08-19")

    assert "## 600519.SH News, from 2026-08-01 to 2026-08-19" in result
    assert "新闻A" in result and "新闻B" in result
    assert "新闻C" not in result  # published 2026-07-01 - outside the window
    # (新闻D is dated 2026-08-19 23:00 - inside; see the inclusive-end test.)


@pytest.mark.unit
def test_news_end_day_is_inclusive(fake_akshare_news):
    result = akshare.get_news("600519.SH", "2026-08-01", "2026-08-19")
    assert "新闻D" in result  # 2026-08-19 23:00 falls on the end date


@pytest.mark.unit
def test_news_empty_window_returns_plain_text(fake_akshare_news):
    result = akshare.get_news("600519.SH", "2025-01-01", "2025-01-31")
    assert result.startswith("No news found for 600519.SH between 2025-01-01 and 2025-01-31")


@pytest.mark.unit
def test_a_share_news_auto_route(fake_akshare_news):
    set_config({"data_vendors": {"news_data": "yfinance"}})
    result = route_to_vendor("get_news", "600519.SH", "2026-08-01", "2026-08-19")
    assert "## 600519.SH News" in result


# --- network resilience -------------------------------------------------------


@pytest.mark.unit
def test_fetch_history_retries_transient_disconnects(monkeypatch):
    """Regression: East Money hosts drop connections intermittently
    (RemoteDisconnected). The first drop used to crash the whole analysis
    run; now the fetch retries and succeeds when the network recovers."""
    import requests as _requests

    calls = {"n": 0}

    def flaky_hist(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _requests.exceptions.ConnectionError(
                "Remote end closed connection without response"
            )
        return _history_frame()

    fake = SimpleNamespace(stock_zh_a_hist=flaky_hist)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    monkeypatch.setattr(akshare.time, "sleep", lambda *_: None)  # no test delay

    result = akshare.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    assert calls["n"] == 2                      # one failure, then success
    assert "# Stock data for 600519" in result


@pytest.mark.unit
def test_fetch_history_raises_after_exhausted_retries(monkeypatch):
    """When the network is genuinely down (both providers unreachable), the
    fetch gives up after the configured attempts on each instead of looping
    forever."""
    import requests as _requests

    calls = {"em": 0, "sina": 0}

    def always_down(**kwargs):
        calls["em"] += 1
        raise _requests.exceptions.ConnectionError("connection refused")

    def sina_down(**kwargs):
        calls["sina"] += 1
        raise _requests.exceptions.ConnectionError("connection refused")

    fake = SimpleNamespace(
        stock_zh_a_hist=always_down,
        stock_zh_a_daily=sina_down,
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    monkeypatch.setattr(akshare.time, "sleep", lambda *_: None)

    with pytest.raises(_requests.exceptions.ConnectionError):
        akshare.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    assert calls["em"] == akshare._FETCH_RETRIES
    assert calls["sina"] == akshare._FETCH_RETRIES


@pytest.mark.unit
def test_no_data_errors_are_not_retried(monkeypatch):
    """Typed vendor errors mean the symbol has no data - retrying would be
    wasted network calls; they must propagate immediately."""
    calls = {"n": 0}

    def empty_hist(**kwargs):
        calls["n"] += 1
        return pd.DataFrame()  # empty -> NoMarketDataError in _fetch_history

    fake = SimpleNamespace(stock_zh_a_hist=empty_hist)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    with pytest.raises(akshare.NoMarketDataError):
        akshare.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    assert calls["n"] == 1  # no retry on typed no-data errors


def _sina_frame():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": range(100, 110),
            "high": range(102, 112),
            "low": range(99, 109),
            "close": range(101, 111),
            "volume": range(1000, 1010),
        }
    )


@pytest.mark.unit
def test_sina_fallback_when_eastmoney_dark(monkeypatch):
    """When East Money's history host is unreachable, the fetch falls back to
    the Sina daily endpoint (different provider/CDN) and normalizes its
    lowercase English columns into the shared OHLCV shape."""
    em_calls = {"n": 0}

    def dark_hist(**kwargs):
        em_calls["n"] += 1
        raise ConnectionError("Remote end closed connection without response")

    def working_daily(**kwargs):
        return _sina_frame()

    fake = SimpleNamespace(
        stock_zh_a_hist=dark_hist,
        stock_zh_a_daily=working_daily,
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    monkeypatch.setattr(akshare.time, "sleep", lambda *_: None)

    result = akshare.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    assert em_calls["n"] == akshare._FETCH_RETRIES  # retried, then fell back
    assert "# Stock data for 600519" in result
    assert "Date,Open,Close,High,Low,Volume" in result  # normalized columns


@pytest.mark.unit
def test_east_money_volume_is_reported_in_shares(monkeypatch):
    """East Money's 成交量 is in 手 (lots of 100); the tool reports shares like
    Sina and Yahoo, so volume-based indicators are comparable either way."""
    frame = pd.DataFrame(
        {
            "日期": ["2026-01-01", "2026-01-02"],
            "开盘": [100, 101],
            "收盘": [101, 102],
            "最高": [102, 103],
            "最低": [99, 100],
            "成交量": [1000, 1100],  # 手
        }
    )
    fake = SimpleNamespace(stock_zh_a_hist=lambda **kwargs: frame)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)

    result = akshare.get_stock("600519.SH", "2026-01-01", "2026-01-02")

    assert "100000" in result   # 1000 手 -> 100,000 shares
    assert "110000" in result


@pytest.mark.unit
def test_sina_volume_stays_in_shares(monkeypatch):
    """The Sina fallback already reports shares and must not be scaled."""
    def dark_hist(**kwargs):
        raise ConnectionError("down")

    def sina_history(**kwargs):
        return pd.DataFrame(
            {
                "date": ["2026-01-01", "2026-01-02"],
                "open": [100, 101],
                "high": [102, 103],
                "low": [99, 100],
                "close": [101, 102],
                "volume": [4177984, 4200000],
            }
        )

    fake = SimpleNamespace(stock_zh_a_hist=dark_hist, stock_zh_a_daily=sina_history)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    monkeypatch.setattr(akshare.time, "sleep", lambda *_: None)

    result = akshare.get_stock("600519.SH", "2026-01-01", "2026-01-02")

    assert "4177984" in result
    assert not any(line.endswith("410000000") for line in result.splitlines())


@pytest.mark.unit
def test_rate_limit_is_not_retried(monkeypatch):
    """A ban is the vendor asking us to stop: retrying extends it. It must fall
    through to Sina after one attempt, not hammer East Money three times."""
    import requests as _requests

    class Push2RateLimitError(_requests.exceptions.ConnectionError):
        pass

    calls = {"em": 0, "sina": 0}

    def limited(**kwargs):
        calls["em"] += 1
        raise Push2RateLimitError("push2 rate limited")

    def sina_history(**kwargs):
        calls["sina"] += 1
        return _sina_frame()

    fake = SimpleNamespace(stock_zh_a_hist=limited, stock_zh_a_daily=sina_history)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    monkeypatch.setattr(akshare.time, "sleep", lambda *_: None)

    result = akshare.get_stock("600519.SH", "2026-01-01", "2026-01-10")

    assert calls["em"] == 1           # no retry ladder on a rate limit
    assert calls["sina"] == 1         # fell back once
    assert "# Stock data for 600519" in result


# --- proxy support ------------------------------------------------------------


@pytest.mark.unit
def test_proxy_from_env_is_applied_to_akshare(monkeypatch):
    """TRADINGAGENTS_AKSHARE_PROXY points akshare's own requests at a proxy
    without touching the process-wide HTTP_PROXY used by other clients."""
    applied = []

    fake = SimpleNamespace(set_proxies=lambda proxies: applied.append(proxies))
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    monkeypatch.setenv(akshare.PROXY_ENV, "socks5h://127.0.0.1:1080")
    monkeypatch.setattr(akshare, "_PROXY_APPLIED", None)

    assert akshare._akshare_module() is fake
    assert applied == [{"http": "socks5h://127.0.0.1:1080", "https": "socks5h://127.0.0.1:1080"}]

    # Applying it again on every call would be pointless work.
    akshare._akshare_module()
    assert len(applied) == 1


@pytest.mark.unit
def test_proxy_setting_is_ignored_when_akshare_cannot_apply_it(monkeypatch):
    """An akshare without set_proxies (older build) must not crash a run."""
    monkeypatch.setitem(__import__("sys").modules, "akshare", SimpleNamespace())
    monkeypatch.setenv(akshare.PROXY_ENV, "http://127.0.0.1:7890")
    monkeypatch.setattr(akshare, "_PROXY_APPLIED", None)

    module = akshare._akshare_module()

    assert module is not None


# --- china-etf: symbol classification ---------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("510300.SH", True),   # 沪深300ETF (Shanghai)
        ("588000.SH", True),   # 科创50ETF (Shanghai STAR)
        ("159915.SZ", True),   # 创业板ETF (Shenzhen)
        ("510300.SS", True),   # Yahoo-form Shanghai suffix
        ("510300", False),     # bare code - ambiguous, not classified
        ("600519.SH", False),  # A-share stock
        ("000001.SZ", False),  # A-share stock
        ("AAPL", False),
        ("0700.HK", False),
    ],
)
def test_is_china_etf_symbol(symbol, expected):
    from tradingagents.dataflows.symbols import is_china_etf_symbol

    assert is_china_etf_symbol(symbol) is expected


# --- china-etf: OHLCV routing -------------------------------------------------


def _etf_em_frame():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": range(400, 410),
            "收盘": range(401, 411),
            "最高": range(402, 412),
            "最低": range(399, 409),
            "成交量": range(10000, 10010),
        }
    )


@pytest.fixture()
def fake_akshare_etf(monkeypatch):
    calls = {"em": 0, "sina": 0}

    fake = SimpleNamespace(
        stock_zh_a_hist=lambda **kw: pytest.fail("stock endpoint must not serve ETFs"),
        fund_etf_hist_em=lambda **kw: (calls.__setitem__("em", calls["em"] + 1), _etf_em_frame())[1],
        fund_etf_hist_sina=lambda **kw: (calls.__setitem__("sina", calls["sina"] + 1), _sina_frame())[1],
        stock_zh_a_daily=lambda **kw: pytest.fail("stock Sina endpoint must not serve ETFs"),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    return calls


@pytest.mark.unit
def test_etf_routes_to_etf_endpoint(fake_akshare_etf):
    """ETF codes must hit the ETF endpoints, never the stock endpoints."""
    result = akshare.get_stock("510300.SH", "2026-01-01", "2026-01-10")

    assert fake_akshare_etf["em"] == 1 and fake_akshare_etf["sina"] == 0
    assert "# Stock data for 510300" in result
    assert "Date,Open,Close,High,Low,Volume" in result


@pytest.mark.unit
def test_etf_sina_fallback_filters_window(fake_akshare_etf, monkeypatch):
    """When East Money's ETF host is down, the Sina ETF endpoint serves the
    data - and its undated full history is filtered to the request window."""
    def dark_em(**kw):
        raise ConnectionError("dark CDN")

    monkeypatch.setitem(
        __import__("sys").modules,
        "akshare",
        SimpleNamespace(
            fund_etf_hist_em=dark_em,
            fund_etf_hist_sina=lambda **kw: _sina_frame(),
            stock_zh_a_hist=lambda **kw: pytest.fail("stock endpoint must not serve ETFs"),
            stock_zh_a_daily=lambda **kw: pytest.fail("stock Sina endpoint must not serve ETFs"),
        ),
    )
    monkeypatch.setattr(akshare.time, "sleep", lambda *_: None)

    result = akshare.get_stock("510300.SH", "2026-01-01", "2026-01-05")

    assert "# Stock data for 510300" in result
    # Window filter dropped rows outside 01-01..01-05 (sina frame spans 10 days).
    data_lines = [line for line in result.splitlines() if line[:4].isdigit()]
    assert all("2026-01-0" in line for line in data_lines)
    assert 1 <= len(data_lines) <= 5


@pytest.mark.unit
def test_etf_indicator_uses_etf_history(fake_akshare_etf):
    """Indicators for ETF codes pull OHLCV through the ETF path too."""
    result = akshare.get_indicator("510300.SH", "macd", "2026-01-10", 5)

    assert "MACD" in result


@pytest.mark.unit
def test_etf_fundamentals_do_not_route_to_akshare(fake_akshare_statements, monkeypatch):
    """Regression: ETF symbols have no financial statements. Routing them to
    AKShare's stock-statement endpoints crashed inside East Money's F10 page
    parser (TypeError on a missing hidctype element) and killed the whole
    analysis run - fundamentals for ETFs must stay on the configured vendor
    (yfinance), which degrades to a clean NO_DATA verdict."""
    set_config({"data_vendors": {"fundamental_data": "yfinance"}})
    import tradingagents.dataflows.router as interface

    monkeypatch.setattr(
        akshare,
        "get_fundamentals",
        lambda *args: pytest.fail("akshare must not serve ETF fundamentals"),
    )
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_fundamentals"], "akshare", akshare.get_fundamentals
    )

    fake_yf = lambda *args: "YFINANCE_FUNDAMENTALS"  # noqa: E731
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_fundamentals"], "yfinance", fake_yf
    )

    result = route_to_vendor("get_fundamentals", "510300.SH", "2026-08-24")

    assert result == "YFINANCE_FUNDAMENTALS"


@pytest.mark.unit
def test_etf_ohlcv_still_routes_to_akshare(fake_akshare_etf):
    """The fundamentals exclusion must not spill into price categories: ETF
    prices/indicators keep their AKShare auto-routing."""
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})

    result = route_to_vendor("get_stock_data", "510300.SH", "2026-01-01", "2026-01-10")

    assert "# Stock data for 510300" in result


# --- akshare macro (China indicators, FRED fallback) -------------------------


def _macro_cpi_frame():
    dates = pd.date_range("2026-04-01", periods=5, freq="MS")
    return pd.DataFrame(
        {
            "商品": ["中国CPI月率报告"] * 5,
            "日期": [d.date() for d in dates],
            "今值": [0.2, 0.3, 0.1, 0.4, 0.2],
            "预测值": [0.2, 0.2, 0.2, 0.3, 0.2],
            "前值": [0.1, 0.2, 0.3, 0.1, 0.4],
        }
    )


@pytest.fixture()
def fake_akshare_macro(monkeypatch):
    fake = SimpleNamespace(
        macro_china_cpi_monthly=lambda: _macro_cpi_frame(),
        macro_china_lpr=lambda: pd.DataFrame(
            {
                "TRADE_DATE": pd.date_range("2026-01-20", periods=8, freq="MS"),
                "LPR1Y": [3.0] * 8,
                "LPR5Y": [3.5] * 8,
            }
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    return fake


@pytest.mark.unit
def test_macro_cpi_report_shape(fake_akshare_macro):
    from tradingagents.dataflows.vendors.akshare_macro import get_macro_data

    result = get_macro_data("cpi", "2026-08-24")

    assert "## AKShare 中国宏观: CPI MoM" in result
    assert "Latest: 0.2" in result
    assert "| Date | Value |" in result


@pytest.mark.unit
def test_macro_lpr_uses_trade_date_column(fake_akshare_macro):
    from tradingagents.dataflows.vendors.akshare_macro import get_macro_data

    result = get_macro_data("lpr", "2026-08-24")

    assert "## AKShare 中国宏观: LPR 1Y" in result
    assert "3.0" in result


@pytest.mark.unit
def test_macro_unknown_indicator_returns_guidance(fake_akshare_macro):
    from tradingagents.dataflows.vendors.akshare_macro import get_macro_data

    result = get_macro_data("bank of japan rate", "2026-08-24")

    assert result.startswith("AKShare macro: unknown indicator")
    assert "For US/global series" in result


@pytest.mark.unit
def test_macro_lookahead_window_filter(fake_akshare_macro):
    """Rows after curr_date must never appear (backtest fidelity)."""
    from tradingagents.dataflows.vendors.akshare_macro import get_macro_data

    result = get_macro_data("cpi", "2026-05-15")  # frame runs to 2026-08

    assert "2026-06" not in result and "2026-08" not in result


@pytest.mark.unit
def test_macro_fallback_chain_fred_first(fake_akshare_macro, monkeypatch):
    """Default config is 'fred,akshare': FRED serves US aliases; AKShare China
    indicators ride the same chain as the configured fallback."""
    set_config({"data_vendors": {"macro_data": "fred,akshare"}})

    import tradingagents.dataflows.router as interface
    import tradingagents.dataflows.vendors.fred as fred_mod

    # FRED raises not-configured -> router falls to akshare for a China alias.
    def _not_configured(*args, **kwargs):
        raise fred_mod.FredNotConfiguredError("no key")

    monkeypatch.setattr(fred_mod, "get_macro_data", _not_configured)
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_macro_indicators"], "fred", _not_configured
    )

    result = route_to_vendor("get_macro_indicators", "cpi", "2026-08-24")

    assert "AKShare 中国宏观" in result
