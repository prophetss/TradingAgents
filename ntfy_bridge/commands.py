"""Parse a phone message into an action.

Only *lexical* checks live here. Whether an analyst name exists, how a ticker is
normalized and which trade dates are legal are the CLI's rules; duplicating them
would drift the moment upstream adds an analyst. Anything this module accepts
but the CLI rejects surfaces as a job failure with the CLI's own message, which
is what the phone sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime

_TICKER_RE = re.compile(r"^[A-Za-z0-9._\-\^=+]{1,32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ANALYSTS_RE = re.compile(r"^[a-z]+(?:,[a-z]+)*$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,40}$")

DEFAULT_ANALYSTS = ("market", "news", "fundamentals", "sentiment")
CONTROL_WORDS = {"help", "status", "rerun", "resend"}
_KEY_NAMES = {"ticker", "date", "analysts", "language", "secret"}


class CommandError(ValueError):
    """The message is understandable but invalid; the sender gets the reason."""


class Unauthorized(CommandError):
    """No valid command secret; the sender gets silence (rate-limited)."""


@dataclass(frozen=True)
class Command:
    kind: str                 # run | help | status | resend | rerun
    ticker: str | None = None
    trade_date: str | None = None
    analysts: tuple[str, ...] = field(default_factory=tuple)
    language: str | None = None
    job_id: str | None = None
    signed: bool = True
    raw: str = ""


def _valid_ticker(value: str) -> str:
    value = value.strip()
    if not _TICKER_RE.match(value):
        raise CommandError(
            f"股票代码格式不对: {value!r}（允许字母/数字/._-^=+，最长 32）"
        )
    if set(value) == {"."}:
        raise CommandError("股票代码不能只是点")
    return value


def _valid_date(value: str, today: date_cls) -> str:
    value = value.strip()
    if not _DATE_RE.match(value):
        raise CommandError(f"日期必须是 YYYY-MM-DD: {value!r}")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"日期不存在: {value!r}") from exc
    if parsed > today:
        raise CommandError(f"日期不能是未来: {value}（今天 {today.isoformat()}）")
    return value


def _valid_analysts(value: str) -> tuple[str, ...]:
    value = value.strip().lower()
    if not _ANALYSTS_RE.match(value):
        raise CommandError(f"分析师列表格式不对: {value!r}（例: market,news）")
    return tuple(part for part in value.split(",") if part)


def _valid_language(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 30 or any(ch in value for ch in "\r\n\t"):
        raise CommandError("language 不能为空、不能含换行，最长 30 字符")
    return value


def _strip_secret(tokens: list[str], secret: str | None) -> tuple[list[str], bool]:
    """Remove the secret token if present; report whether the command is signed."""
    if not secret:
        return tokens, True
    if tokens and tokens[0] == secret:
        return tokens[1:], True
    for index, token in enumerate(tokens):
        if token.startswith("secret=") and token.split("=", 1)[1] == secret:
            return tokens[:index] + tokens[index + 1 :], True
    return tokens, False


def parse(text: str, *, secret: str | None = None, today: date_cls | None = None) -> Command:
    """Parse one message. Raises :class:`CommandError`/`Unauthorized`."""
    today = today or date_cls.today()
    raw = (text or "").strip()
    if not raw:
        raise CommandError("空命令")

    tokens = raw.split()
    tokens, signed = _strip_secret(tokens, secret)
    if not tokens:
        raise CommandError("缺少命令内容")

    word = tokens[0].lower()
    if word == "help":
        if len(tokens) > 1:
            raise CommandError("用法: help")
        return Command(kind="help", signed=signed, raw=raw)
    if not signed:
        raise Unauthorized("missing or wrong command secret")

    if word == "status":
        if len(tokens) > 1:
            raise CommandError("用法: status")
        return Command(kind="status", signed=True, raw=raw)

    if word == "resend":
        if len(tokens) != 2 or not _JOB_ID_RE.match(tokens[1]):
            raise CommandError("用法: resend <job id>")
        return Command(kind="resend", job_id=tokens[1], signed=True, raw=raw)

    if word == "rerun":
        kind = "rerun"
        tokens = tokens[1:]
    else:
        kind = "run"

    # key=value form overrides positional values.
    keyed: dict[str, str] = {}
    positional: list[str] = []
    for token in tokens:
        if "=" in token and token.split("=", 1)[0].lower() in _KEY_NAMES:
            key, value = token.split("=", 1)
            keyed[key.lower()] = value
        else:
            positional.append(token)

    ticker = keyed.get("ticker") or (positional.pop(0) if positional else None)
    if ticker is None:
        raise CommandError("缺少股票代码")
    trade_date = keyed.get("date") or (positional.pop(0) if positional else None)
    analysts_value = keyed.get("analysts") or (positional.pop(0) if positional else None)
    language = keyed.get("language")
    if positional:
        raise CommandError(f"多余参数: {' '.join(positional)}")

    return Command(
        kind=kind,
        ticker=_valid_ticker(ticker),
        trade_date=_valid_date(trade_date, today) if trade_date else today.isoformat(),
        analysts=_valid_analysts(analysts_value) if analysts_value else DEFAULT_ANALYSTS,
        language=_valid_language(language) if language else None,
        signed=True,
        raw=raw,
    )
