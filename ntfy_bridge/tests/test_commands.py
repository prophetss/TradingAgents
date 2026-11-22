from __future__ import annotations

from datetime import date

import pytest

from ntfy_bridge.commands import CommandError, Unauthorized, parse

TODAY = date(2026, 11, 20)


def test_plain_ticker_uses_today_and_all_analysts():
    cmd = parse("600519.SH", today=TODAY)
    assert cmd.kind == "run"
    assert cmd.ticker == "600519.SH"
    assert cmd.trade_date == "2026-11-20"
    assert cmd.analysts == ("market", "news", "fundamentals", "sentiment")


def test_positional_date_and_analysts():
    cmd = parse("600519.SH 2026-11-01 market,news", today=TODAY)
    assert cmd.trade_date == "2026-11-01"
    assert cmd.analysts == ("market", "news")


def test_key_value_form():
    cmd = parse("ticker=AAPL date=2026-11-02 analysts=market language=Chinese", today=TODAY)
    assert cmd.ticker == "AAPL"
    assert cmd.trade_date == "2026-11-02"
    assert cmd.analysts == ("market",)
    assert cmd.language == "Chinese"


def test_secret_can_be_prefix_or_key():
    prefixed = parse("s3cret 600519.SH", secret="s3cret", today=TODAY)
    keyed = parse("600519.SH secret=s3cret", secret="s3cret", today=TODAY)
    assert prefixed.ticker == keyed.ticker == "600519.SH"


def test_missing_secret_is_unauthorized():
    with pytest.raises(Unauthorized):
        parse("600519.SH", secret="s3cret", today=TODAY)


def test_help_is_allowed_without_secret_but_status_is_not():
    assert parse("help", secret="s3cret", today=TODAY).kind == "help"
    with pytest.raises(Unauthorized):
        parse("status", secret="s3cret", today=TODAY)
    assert parse("s3cret status", secret="s3cret", today=TODAY).kind == "status"


def test_rerun_bypasses_flag_and_resend_needs_job_id():
    rerun = parse("rerun 600519.SH 2026-11-01", today=TODAY)
    assert rerun.kind == "rerun"
    assert rerun.trade_date == "2026-11-01"
    resend = parse("resend 20261120-120000-ab12cd", today=TODAY)
    assert resend.kind == "resend"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "600519.SH 2026-13-01",       # impossible date
        "600519.SH 2026-11-21",       # future
        "600519/../../etc 2026-11-01",  # not a ticker
        "...",
        "600519.SH 2026-11-01 Market News",  # analysts must be comma-separated lowercase
        "resend",
        "help me",
    ],
)
def test_invalid_commands_raise(text):
    with pytest.raises(CommandError):
        parse(text, today=TODAY)


def test_long_ticker_rejected():
    with pytest.raises(CommandError):
        parse("A" * 33, today=TODAY)
