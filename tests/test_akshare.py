from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import akshare
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import route_to_vendor


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
        __import__("tradingagents.dataflows.interface", fromlist=["VENDOR_METHODS"]).VENDOR_METHODS,
        {"get_stock_data": {"akshare": akshare.get_stock, "yfinance": lambda *args: "YFINANCE_DATA"}},
        clear=False,
    ):
        result = route_to_vendor("get_stock_data", "600000.SH", "2026-01-01", "2026-01-10")

    assert "# Stock data for 600000" in result


@pytest.mark.unit
def test_sz_suffix_automatically_routes_to_akshare(fake_akshare):
    set_config({"data_vendors": {"technical_indicators": "yfinance"}})

    with mock.patch.dict(
        __import__("tradingagents.dataflows.interface", fromlist=["VENDOR_METHODS"]).VENDOR_METHODS,
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
    import tradingagents.dataflows.interface as interface
    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", lambda *args: "YFINANCE_DATA")

    result = route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-01-10")

    assert result == "YFINANCE_DATA"
