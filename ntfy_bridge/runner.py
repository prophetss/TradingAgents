"""Run one analysis as a subprocess and read back its artifacts.

The CLI is treated as a black box: we pass headless flags and a per-job
``TRADINGAGENTS_RESULTS_DIR``, then read the state log and report tree it
writes. No TradingAgents modules are imported, so upstream refactors cannot
break this file except through those two paths (the declared coupling).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import formatting
from .config import Config
from .jobs import new_job_id

logger = logging.getLogger(__name__)

# Enough of the log for a failure notice; the service scrubs and truncates it.
LOG_TAIL_CHARS = 4000


@dataclass
class RunResult:
    job_id: str
    exit_code: int
    timed_out: bool
    duration: float
    rating: str | None = None
    decision: str | None = None
    report_path: Path | None = None
    state_path: Path | None = None
    degraded: bool = False
    log_path: Path | None = None
    log_tail: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error == ""


def _git(*args: str, cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def git_state(config: Config) -> tuple[str | None, bool]:
    """(branch, dev_build). Dev-build means the live checkout is not a clean default branch.

    Untracked files are ignored: running Python from the repo creates
    ``__pycache__`` that would otherwise mark every run as a dev build and turn
    the warning into noise nobody reads. A missing ``.git`` disables the check.
    """
    repo = config.repo_root
    if not (repo / ".git").exists():
        return None, False
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    if branch is None:
        return None, False
    detached = branch == "HEAD"
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no", cwd=repo))
    dev = detached or dirty or (config.default_branch and branch != config.default_branch)
    return branch, dev


def build_argv(config: Config, job: dict) -> list[str]:
    """The headless CLI invocation for one job."""
    return [
        config.cli,
        "--ticker", job["ticker"],
        "--date", job["trade_date"],
        "--analysts", ",".join(job["analysts"]),
        "--save",
        "--no-show",
        "--no-html",
        "--clear-checkpoints",
    ]


def build_env(config: Config, job: dict, results_dir: Path) -> dict[str, str]:
    """The child environment: no bridge credentials, stable artifact paths."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("NTFY_")}
    env["TRADINGAGENTS_RESULTS_DIR"] = str(results_dir)
    env["PYTHONUNBUFFERED"] = "1"
    language = job.get("language") or config.default_language
    if language:
        env["TRADINGAGENTS_OUTPUT_LANGUAGE"] = language
    return env


def run_job(job: dict, config: Config) -> RunResult:
    """Execute the analysis for ``job``; never raises for a failed run."""
    started = time.monotonic()
    job_id = job["id"]
    job_dir = config.home / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    results_dir = job_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "log.txt"

    argv = build_argv(config, job)
    env = build_env(config, job, results_dir)
    logger.info("job %s: %s", job_id, " ".join(argv))

    timed_out = False
    exit_code = -1
    with open(log_path, "wb") as log:
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(config.repo_root),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            return RunResult(
                job_id=job_id, exit_code=-1, timed_out=False,
                duration=time.monotonic() - started,
                log_path=log_path, error=f"无法启动 CLI: {exc}",
            )
        try:
            exit_code = process.wait(timeout=config.job_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(process)
            exit_code = process.returncode if process.returncode is not None else -1

    duration = time.monotonic() - started
    result = RunResult(
        job_id=job_id,
        exit_code=exit_code,
        timed_out=timed_out,
        duration=duration,
        log_path=log_path,
        log_tail=_tail(log_path),
    )

    result.state_path = _newest(results_dir.glob("*/TradingAgentsStrategy_logs/full_states_log_*.json"))
    result.report_path = _newest(results_dir.glob("reports/*/complete_report.md"))

    if result.state_path:
        state = _read_json(result.state_path)
        if isinstance(state, dict):
            rating = state.get("final_rating")
            result.rating = rating if isinstance(rating, str) and rating.strip() else None
            decision = state.get("final_trade_decision")
            result.decision = decision if isinstance(decision, str) else None
            report_text = _read_text(result.report_path) if result.report_path else ""
            result.degraded = formatting.is_degraded(result.decision, report_text,
                                                     json.dumps(state, ensure_ascii=False))
        else:
            result.error = "状态日志无法解析"
    elif result.report_path:
        # Newer upstream may change the state-log layout; the report is enough.
        result.degraded = formatting.is_degraded(_read_text(result.report_path))
    elif not timed_out and exit_code == 0:
        result.error = "运行成功但未找到报告文件（上游路径可能变更）"

    if timed_out:
        result.error = f"运行超时（{config.job_timeout}s）已终止"
    elif exit_code != 0 and not result.error:
        result.error = f"CLI 退出码 {exit_code}"
    return result


def _terminate_group(process: subprocess.Popen) -> None:
    """SIGTERM the whole session, then SIGKILL after a short grace period."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()


def _newest(paths) -> Path | None:
    candidates = [path for path in paths if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path, attempts: int = 2) -> dict | None:
    """Read the state log, retrying once: the CLI writes it non-atomically."""
    for attempt in range(attempts):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            if attempt + 1 < attempts:
                time.sleep(0.25)
    return None


def _tail(path: Path) -> str:
    text = _read_text(path)
    return text[-LOG_TAIL_CHARS:]


def job_id_for(now=None) -> str:
    return new_job_id(now)
