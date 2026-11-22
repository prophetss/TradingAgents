"""Environment-only configuration, validated before the daemon starts."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

TOPIC_RE = re.compile(r"^[-_A-Za-z0-9]{1,64}$")
DEFAULT_ANALYSTS = "market,news,fundamentals,sentiment"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _int(env: dict, name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，收到 {raw!r}") from exc


def _bool(env: dict, name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    server: str
    command_topic: str
    result_topic: str
    token: str | None = None
    command_secret: str | None = None
    home: Path = field(default_factory=lambda: Path.home() / ".tradingagents" / "ntfy-bridge")
    repo_root: Path = _REPO_ROOT
    cli: str = ""
    default_branch: str = "main"
    daily_job_limit: int = 10
    queue_limit: int = 10
    job_timeout: int = 1800
    min_job_gap: int = 30
    rate_limit_backoff: int = 600
    replay_max_age: int = 3600
    retention_days: int = 14
    heartbeat_hour: int = 9
    poll_interval: int = 5
    default_language: str | None = None
    count_failed: bool = True
    title_prefix: str = ""

    # -- derived paths -----------------------------------------------------
    @property
    def jobs_dir(self) -> Path:
        return self.home / "jobs"

    @property
    def outbox_dir(self) -> Path:
        return self.home / "outbox"

    @property
    def dead_letter_dir(self) -> Path:
        return self.home / "dead-letter"

    @property
    def queue_file(self) -> Path:
        return self.home / "queue.json"

    @property
    def seen_file(self) -> Path:
        return self.home / "seen.json"

    @property
    def stats_file(self) -> Path:
        return self.home / "stats.json"

    @property
    def secrets(self) -> tuple[str, ...]:
        return tuple(value for value in (self.token, self.command_secret) if value)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_env(cls, env: dict | None = None) -> Config:
        env = dict(os.environ if env is None else env)

        def text(name: str, default: str = "") -> str:
            return env.get(name, "").strip() or default

        repo_root = Path(text("NTFY_BRIDGE_REPO", str(_REPO_ROOT))).expanduser().resolve()
        cli = text("NTFY_CLI")
        if not cli:
            candidate = repo_root / ".venv" / "bin" / "tradingagents"
            cli = str(candidate) if candidate.exists() else (shutil.which("tradingagents") or "tradingagents")

        home = Path(text("NTFY_BRIDGE_HOME", "~/.tradingagents/ntfy-bridge")).expanduser()
        heartbeat = _int(env, "NTFY_HEARTBEAT_HOUR", 9)

        return cls(
            server=text("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
            command_topic=text("NTFY_COMMAND_TOPIC"),
            result_topic=text("NTFY_RESULT_TOPIC"),
            token=text("NTFY_TOKEN") or None,
            command_secret=text("NTFY_COMMAND_SECRET") or None,
            home=home.resolve(),
            repo_root=repo_root,
            cli=cli,
            default_branch=text("NTFY_BRANCH", "main"),
            daily_job_limit=_int(env, "NTFY_DAILY_JOB_LIMIT", 10),
            queue_limit=_int(env, "NTFY_QUEUE_LIMIT", 10),
            job_timeout=_int(env, "NTFY_JOB_TIMEOUT", 1800),
            min_job_gap=_int(env, "NTFY_MIN_JOB_GAP", 30),
            rate_limit_backoff=_int(env, "NTFY_RATE_LIMIT_BACKOFF", 600),
            replay_max_age=_int(env, "NTFY_REPLAY_MAX_AGE", 3600),
            retention_days=_int(env, "NTFY_RETENTION_DAYS", 14),
            heartbeat_hour=heartbeat if 0 <= heartbeat <= 23 else -1,
            poll_interval=_int(env, "NTFY_POLL_INTERVAL", 5),
            default_language=text("NTFY_OUTPUT_LANGUAGE") or None,
            count_failed=_bool(env, "NTFY_COUNT_FAILED", True),
            title_prefix=text("NTFY_TITLE_PREFIX"),
        )

    # -- validation --------------------------------------------------------
    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.server.startswith(("http://", "https://")):
            found.append("NTFY_SERVER 必须以 http:// 或 https:// 开头")
        if not TOPIC_RE.match(self.command_topic):
            found.append("NTFY_COMMAND_TOPIC 缺失或含非法字符（允许字母数字 - _，≤64）")
        if not TOPIC_RE.match(self.result_topic):
            found.append("NTFY_RESULT_TOPIC 缺失或含非法字符（允许字母数字 - _，≤64）")
        if self.command_topic and self.command_topic == self.result_topic:
            found.append("command 与 result 建议使用不同 topic")
        if not (self.repo_root / ".git").exists() and not self.repo_root.exists():
            found.append(f"仓库目录不存在: {self.repo_root}")
        if self.daily_job_limit < 0:
            found.append("NTFY_DAILY_JOB_LIMIT 不能为负")
        if self.queue_limit < 1:
            found.append("NTFY_QUEUE_LIMIT 至少为 1")
        if self.job_timeout < 60:
            found.append("NTFY_JOB_TIMEOUT 至少 60 秒")
        if self.poll_interval < 1:
            found.append("NTFY_POLL_INTERVAL 至少 1 秒")
        return found

    def describe(self) -> str:
        """Human-readable settings with secrets masked (used by ``doctor``)."""
        def mask(value: str | None) -> str:
            if not value:
                return "(unset)"
            return value[:4] + "***" if len(value) > 4 else "***"

        return "\n".join(
            [
                f"server          {self.server}",
                f"command topic   {self.command_topic or '(unset)'}",
                f"result topic    {self.result_topic or '(unset)'}",
                f"token           {mask(self.token)}",
                f"command secret  {mask(self.command_secret)}",
                f"home            {self.home}",
                f"repo root       {self.repo_root}",
                f"cli             {self.cli}",
                f"branch          {self.default_branch}",
                f"daily limit     {self.daily_job_limit}",
                f"queue limit     {self.queue_limit}",
                f"job timeout     {self.job_timeout}s",
                f"heartbeat hour  {self.heartbeat_hour}",
                f"poll interval   {self.poll_interval}s",
                f"default analysts {DEFAULT_ANALYSTS}",
            ]
        )
