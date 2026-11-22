"""Turn a run's result into notification-safe text.

Everything here is sized for ntfy's limits *after* URL encoding: the message and
title travel as query parameters (headers are latin-1 in ``requests`` and the
reports are often Chinese), and a 3-byte CJK character becomes ~9 bytes once
percent-encoded. A raw-UTF-8 budget therefore overflows the request line, which
servers reject at ~8 KB.
"""

from __future__ import annotations

import re
from urllib.parse import quote

# Conservative caps, well under ntfy's 4 KB body and common 8 KB request-line
# limits, with room for the other parameters and headers.
MAX_MESSAGE_ENCODED_BYTES = 2000
MAX_TITLE_ENCODED_BYTES = 200

# The router emits these ASCII sentinels when a vendor could not serve a call;
# they are language-independent even when the surrounding report is not.
DEGRADED_MARKERS = ("NO_DATA_AVAILABLE", "DATA_UNAVAILABLE")

_RATING_TAGS = {
    "buy": "chart_with_upwards_trend",
    "overweight": "chart_with_upwards_trend",
    "hold": "straight_ruler",
    "underweight": "chart_with_downwards_trend",
    "sell": "chart_with_downwards_trend",
    "review": "question",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def encoded_len(text: str) -> int:
    """Byte length of ``text`` once percent-encoded as a query-parameter value."""
    return len(quote(text, safe=""))


def truncate_encoded(text: str, limit: int = MAX_MESSAGE_ENCODED_BYTES) -> str:
    """Cut ``text`` so its percent-encoded form fits ``limit`` bytes.

    Cuts on a character boundary and appends an ellipsis when anything was
    dropped, so a multi-byte character is never split.
    """
    text = text or ""
    if encoded_len(text) <= limit:
        return text
    ellipsis = "…"
    budget = max(limit - encoded_len(ellipsis), 0)
    used = 0
    out: list[str] = []
    for char in text:
        size = encoded_len(char)
        if used + size > budget:
            break
        out.append(char)
        used += size
    return "".join(out).rstrip() + ellipsis


def clean_title(text: str, limit: int = MAX_TITLE_ENCODED_BYTES) -> str:
    """One-line, control-character-free title that fits the encoded budget."""
    cleaned = _CONTROL_CHARS.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned or "TradingAgents"
    return truncate_encoded(cleaned, limit)


def rating_tag(rating: str | None) -> str:
    """An ntfy emoji shortcode (ASCII) for the rating's direction."""
    return _RATING_TAGS.get((rating or "").strip().lower(), "question")


def clean_rating(rating: str | None) -> str:
    """The rating as a short single-line label, or ``REVIEW`` when unreadable."""
    if not isinstance(rating, str):
        return "REVIEW"
    cleaned = _CONTROL_CHARS.sub(" ", rating)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "REVIEW"
    return cleaned[:40]


def attachment_name(ticker: str, trade_date: str, job_id: str) -> str:
    """An ASCII-only ``.md`` filename for the attachment header."""
    safe_ticker = re.sub(r"[^A-Za-z0-9._-]", "_", ticker or "ticker").strip("._") or "ticker"
    safe_date = re.sub(r"[^0-9-]", "", trade_date or "") or "date"
    return f"{safe_ticker}_{safe_date}_{job_id[:8]}.md"


def is_degraded(*texts: str | None) -> bool:
    """Whether any report text carries a data-unavailable sentinel."""
    return any(marker in (text or "") for text in texts for marker in DEGRADED_MARKERS)


def build_summary(
    *,
    ticker: str,
    trade_date: str,
    rating: str | None,
    decision: str | None,
    job_id: str,
    degraded: bool = False,
    dev_build: bool = False,
    report_name: str | None = None,
) -> str:
    """The notification body: rating, caveats, then the decision text."""
    lines = [f"**{clean_rating(rating)}** · {ticker} · {trade_date}"]
    if degraded:
        lines.append("⚠️ 部分数据不可用（报告中含 NO_DATA / DATA_UNAVAILABLE）")
    if dev_build:
        lines.append("⚠️ dev-build：本次运行使用的是当前工作区代码（非干净主线）")
    lines.append(f"job: `{job_id}`")
    if report_name:
        lines.append(f"附件: `{report_name}`")
    lines.append("")
    body = (decision or "").strip() or "（报告未包含最终决策文本，请查看附件）"
    header = "\n".join(lines)
    # Size the body to whatever the header leaves, then hard-cap the whole thing.
    remaining = MAX_MESSAGE_ENCODED_BYTES - encoded_len(header) - encoded_len("\n")
    summary = header + "\n" + truncate_encoded(body, max(remaining, 200))
    return truncate_encoded(summary, MAX_MESSAGE_ENCODED_BYTES)


def build_title(ticker: str, trade_date: str, rating: str | None, *, prefix: str = "") -> str:
    """The one-line notification title."""
    parts = [part for part in (prefix, f"{ticker} {trade_date}", clean_rating(rating)) if part]
    return clean_title(" · ".join(parts))


USAGE = """\
可用命令（发到 command topic）：
• <代码> [日期] [分析师]   例: 600519.SH 2026-11-20 market,news
• ticker=… date=… analysts=… language=…
• rerun <代码> [日期]      绕过当日去重重跑
• resend <job id>          重发已有报告附件（不重跑）
• status                   队列与今日用量
• help                     本说明
分析师: market,news,fundamentals,sentiment；日期缺省为今天。
""".strip()
