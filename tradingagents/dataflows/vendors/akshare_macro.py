"""AKShare macro vendor - Chinese macroeconomic indicators.

Serves the ``macro_data`` category as a fallback (or alternative) to FRED:
FRED covers US/global series, while this vendor exposes China-specific
indicators (CPI, LPR, M2, GDP, PMI, ...) via AKShare's East Money/统计局限域,
which is keyless and reachable from mainland networks.

Mirrors the FRED adapter's contract: ``get_macro_data(indicator, curr_date,
look_back_days)`` returning a markdown report with header, latest value,
window change, and a recent observation table. Unknown indicators return
guidance text instead of raising, so a bad LLM argument never aborts the run.
"""

from __future__ import annotations

import logging

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.vendors.akshare import _akshare_module, _with_retries

logger = logging.getLogger(__name__)

# Rows cap for the rendered table, mirroring the FRED adapter.
MAX_ROWS = 40

# Indicator alias -> (fetcher name, value column, value label, units, frequency).
# Value columns verified live against akshare 1.20.65 (2026-11): the per-release
# macro_china_cpi_monthly/_yearly, macro_china_ppi_yearly, macro_china_gdp_yearly
# and macro_china_pmi_yearly endpoints were removed in 1.20.42, and
# macro_china_lpr / macro_china_money_supply were renamed. The replacements below
# return one table per indicator family with month-on-month, year-on-year and
# level columns side by side, so the alias picks the column it needs.
MACRO_SERIES = {
    # Prices
    "cpi": ("macro_china_cpi", "全国-环比增长", "CPI MoM", "%", "monthly"),
    "cpi_yoy": ("macro_china_cpi", "全国-同比增长", "CPI YoY", "%", "monthly"),
    "ppi": ("macro_china_ppi", "当月同比增长", "PPI YoY", "%", "monthly"),
    # Rates
    "lpr": ("interest_rate_lpr_em", "1年期LPR", "LPR 1Y", "%", "monthly"),
    "lpr_5y": ("interest_rate_lpr_em", "5年期以上LPR", "LPR 5Y", "%", "monthly"),
    # Growth
    "gdp": ("macro_china_gdp", "国内生产总值-同比增长", "GDP YoY", "%", "quarterly"),
    # Money
    "m2": ("macro_china_money_supply_em", "货币和准货币(M2)-同比增长", "M2 YoY", "%", "monthly"),
    "m1": ("macro_china_money_supply_em", "货币(M1)-同比增长", "M1 YoY", "%", "monthly"),
    # Business sentiment (an index, not a percentage)
    "pmi": ("macro_china_pmi", "制造业-指数", "Manufacturing PMI", "index", "monthly"),
}

# These endpoints key their rows by a human-readable period instead of a date:
# 月份 ("2026年10月份" for CPI/PPI/PMI/money supply) and 季度
# ("2026年第1-3季度" for GDP). LPR uses a real 日期 cell. Only rows with a
# numeric value are kept, so 未披露 placeholders drop out naturally.

_QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def _quarter_end(row) -> pd.Timestamp:
    """End of the last quarter a cumulative row covers ("2026年第1-3季度" -> 2026-09-30)."""
    if pd.isna(row[0]):
        return pd.NaT
    last_quarter = int(row[2] if not pd.isna(row[2]) else row[1])
    year = int(row[0])
    month = _QUARTER_END_MONTH.get(last_quarter, 12)
    return pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)


def _normalize_date_column(frame: pd.DataFrame) -> pd.Series:
    """Extract a datetime series from whichever period column the endpoint uses."""
    for column in ("日期", "月份", "季度", "TRADE_DATE"):
        if column in frame.columns:
            values = frame[column]
            if column == "月份":
                # "2008年01月份" -> 2008-01-01
                parsed = (
                    values.astype(str)
                    .str.extract(r"(\d{4})年(\d{1,2})月")
                    .apply(lambda r: pd.NaT if pd.isna(r[0]) else pd.Timestamp(int(r[0]), int(r[1]), 1), axis=1)
                )
                return parsed
            if column == "季度":
                # "2026年第1-3季度" -> 2026-09-30; "2026年第1季度" -> 2026-03-31
                decades = values.astype(str).str.extract(r"(\d{4})年第(\d)(?:-(\d))?季度")
                return decades.apply(_quarter_end, axis=1)
            return pd.to_datetime(values, errors="coerce")
    return pd.Series(pd.NaT, index=frame.index)


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Fetch a Chinese macroeconomic series as a formatted markdown report.

    Args:
        indicator: One of the aliases in ``MACRO_SERIES`` (e.g. "cpi", "lpr",
            "m2", "gdp"). Unknown indicators return guidance text, not an error.
        curr_date: End of the window (yyyy-mm-dd); later rows are dropped so a
            historical run never sees future data.
        look_back_days: Trailing window length; ``None`` keeps all rows (the
            endpoints already return bounded history).

    Returns:
        A markdown report shaped like the FRED adapter's output.
    """
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in MACRO_SERIES:
        return (
            f"AKShare macro: unknown indicator '{indicator}'. Supported China "
            f"indicators: {', '.join(sorted(MACRO_SERIES))}. For US/global series "
            f"use a FRED alias or series ID."
        )

    fetcher_name, value_column, label, units, frequency = MACRO_SERIES[key]
    try:
        fetcher = getattr(_akshare_module(), fetcher_name)
    except ImportError as exc:
        raise VendorNotConfiguredError(
            "AKShare is not installed. Install with: pip install 'tradingagents[akshare]'"
        ) from exc
    frame = _with_retries(f"macro {key}", fetcher)

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise NoMarketDataError(indicator, key, f"{fetcher_name} returned no rows")
    if value_column not in frame.columns:
        raise NoMarketDataError(
            indicator, key, f"{fetcher_name} is missing column {value_column!r}"
        )

    end_dt = pd.Timestamp(curr_date)
    dates = _normalize_date_column(frame)
    values = pd.to_numeric(frame[value_column], errors="coerce")

    work = pd.DataFrame({"date": dates, "value": values}).dropna(subset=["date", "value"])
    if look_back_days is not None:
        start_dt = end_dt - pd.Timedelta(days=look_back_days)
        work = work[work["date"] >= start_dt]
    work = work[work["date"] <= end_dt].sort_values("date")

    header = (
        f"## AKShare 中国宏观: {label}\n"
        f"- Units: {units}\n"
        f"- Frequency: {frequency}\n"
        f"- Window: up to {curr_date}\n"
    )

    if work.empty:
        return header + (
            f"\nNo observations for {label} in this window. The series may report "
            f"less frequently than the window; widen look_back_days."
        )

    latest = work.iloc[-1]
    window_change = float(latest["value"] - work.iloc[0]["value"])

    lines = [header]
    lines.append(f"- Latest: {latest['value']} ({latest['date'].date()})")
    lines.append(f"- Change over window: {window_change:+.2f}")
    lines.append("")
    lines.append("| Date | Value |")
    lines.append("|---|---|")
    for _, row in work.tail(MAX_ROWS).iterrows():
        lines.append(f"| {row['date'].date()} | {row['value']} |")
    return "\n".join(lines)
