"""The daemon: listen, queue, run, publish.

Three daemon threads plus a supervisor:

* listener - drains the backlog once, then keeps the JSON stream open; a read
  timeout above ntfy's keepalive turns a black-holed connection into a raise;
* worker   - runs one job at a time through the CLI subprocess;
* notifier - flushes the outbox, sends the daily heartbeat, watches freshness.

The supervisor thread only pings systemd's watchdog and notices a dead worker,
so liveness checks cannot be blocked by a long analysis.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import date as date_cls, datetime
from pathlib import Path

from . import formatting
from .client import NtfyClient, NtfyError
from .commands import Command, CommandError, Unauthorized, parse
from .config import Config
from .jobs import FAILED, Store, new_job_id
from .runner import RunResult, git_state, run_job
from .scrub import scrub

logger = logging.getLogger(__name__)

# The listener polls instead of holding a stream open: proxies in front of
# ntfy.sh buffer chunked responses for minutes, so a "live" stream can sit
# silent while the connection looks healthy. A poll with ?since=<id> is small,
# works through any proxy, and loses at most one interval of latency.
UNHEALTHY_NOTICE_INTERVAL = 3600
EXPIRED_NOTICE_INTERVAL = 3600
REJECTION_REPLY_INTERVAL = 600
MAX_OUTBOX_ATTEMPTS = 6
WORKER_IDLE_SLEEP = 1.0
NOTIFIER_SLEEP = 30.0


class Service:
    def __init__(
        self,
        config: Config,
        *,
        client: NtfyClient | None = None,
        store: Store | None = None,
        runner=run_job,
    ):
        self.config = config
        self.client = client or NtfyClient(config.server, config.token)
        self.store = store or Store(config.home)
        self.runner = runner
        self._stop = threading.Event()
        self._listener_beat = 0.0
        self._last_unhealthy_notice = 0.0
        self._last_reject_reply = 0.0
        self._expired = 0
        self._last_expired_notice = 0.0
        self._gap_checked = False
        self._worker_busy_until = 0.0
        self._started_ts = time.time()
        self._secrets = config.secrets
        self._threads: list[threading.Thread] = []
        self._pruned_day = ""

    # -- public entry points ----------------------------------------------
    def serve_forever(self) -> None:
        problems = self.config.problems()
        if problems:
            raise SystemExit("配置有误:\n- " + "\n- ".join(problems))
        self._install_signal_handlers()
        self._start_threads()
        sd_notify("READY=1")
        logger.info("ntfy bridge listening on %s/%s", self.config.server, self.config.command_topic)
        watchdog = _watchdog_seconds()
        interval = max(1.0, min(watchdog / 3 if watchdog else 10.0, 10.0))
        try:
            while not self._stop.is_set():
                sd_notify("WATCHDOG=1")
                self._notice_dead_threads()
                time.sleep(interval)
        finally:
            self._stop.set()
            self._join_threads()

    def run_once(self) -> int:
        """Process the backlog and every queued job, then return an exit code."""
        problems = self.config.problems()
        if problems:
            logger.error("配置有误: %s", "; ".join(problems))
            return 2
        self._poll_once()
        while not self._stop.is_set():
            job = self.store.next_pending()
            if job is None:
                break
            self._run_job(job)
        self.flush_outbox()
        self.maybe_heartbeat()
        return 0

    # -- threads -----------------------------------------------------------
    def _start_threads(self) -> None:
        for name, target in (
            ("listener", self._listen_loop),
            ("worker", self._worker_loop),
            ("notifier", self._notifier_loop),
        ):
            thread = threading.Thread(target=target, name=f"ntfy-{name}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def _join_threads(self) -> None:
        for thread in self._threads:
            thread.join(timeout=5)

    def _notice_dead_threads(self) -> None:
        dead = [thread.name for thread in self._threads if not thread.is_alive()]
        if dead and not self._stop.is_set():
            logger.error("threads exited unexpectedly: %s", ", ".join(dead))
            self._stop.set()

    # -- listener ----------------------------------------------------------
    def _listen_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._poll_once(timeout=max(10, self.config.poll_interval * 2))
                backoff = 1.0
            except NtfyError as exc:
                logger.warning("poll failed: %s", exc)
                if exc.permanent:
                    logger.error("subscription rejected permanently; check topic/token settings")
                    self._stop.wait(60)
                    continue
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            except Exception:  # keep the daemon alive; log with traceback
                logger.exception("listener failed while handling a poll")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            self._stop.wait(self.config.poll_interval)
        self._listener_beat = 0.0

    def _poll_once(self, *, timeout: float | None = None) -> None:
        """Read everything new since the last seen message and handle it."""
        since = self.store.last_seen() or str(int(self._started_ts))
        if not self._gap_checked:
            self._gap_checked = True
            self._report_downtime_gap()
        events = self.client.poll(self.config.command_topic, since=since, timeout=timeout)
        self._listener_beat = time.time()
        self._expired = 0
        for event in events:
            self._handle_event(event)
        if self._expired:
            self._report_expired()

    def _report_downtime_gap(self) -> None:
        """Say so when the machine was off longer than ntfy keeps its cache.

        Messages evicted from the server cache are invisible to us, so this is
        the only way the gap can reach the phone instead of staying silent.
        """
        last = self.store.last_seen_ts()
        if not last:
            return
        gap = time.time() - last
        if gap <= 12 * 3600:
            return
        self.notify_text(
            f"bridge 停机约 {gap / 3600:.1f} 小时（上次处理消息距今）。"
            "ntfy 仅缓存约 12 小时，期间发送的命令可能已丢失。",
            title="Bridge 停机提示",
            tags=("warning",),
        )

    def _report_expired(self) -> None:
        now = time.time()
        if now - self._last_expired_notice < EXPIRED_NOTICE_INTERVAL:
            return
        self._last_expired_notice = now
        message = f"有 {self._expired} 条命令已过期（超过 {self.config.replay_max_age // 60} 分钟）未执行"
        self.notify_text(message, title="命令已过期", tags=("hourglass",))

    def _handle_event(self, event: dict) -> None:
        if event.get("event") != "message":
            return
        message_id = event.get("id")
        event_time = float(event.get("time") or time.time())
        text = event.get("message") or ""

        if time.time() - event_time > self.config.replay_max_age:
            self._expired += 1
            self.store.set_seen(message_id, event_time)
            return

        try:
            command = parse(text, secret=self.config.command_secret)
        except Unauthorized:
            self.store.record_rejected()
            self._maybe_reject_reply()
            self.store.set_seen(message_id, event_time)
            return
        except CommandError as exc:
            if self.config.command_secret and not _looks_signed(text, self.config.command_secret):
                self.store.record_rejected()
                self._maybe_reject_reply()
            else:
                self.notify_text(f"命令无效：{exc}", title="命令被拒绝", tags=("no_entry_sign",))
            self.store.set_seen(message_id, event_time)
            return

        try:
            self._dispatch(command, message_id)
        except Exception:
            logger.exception("failed to handle command %r", text[:80])
        finally:
            # Mark seen even on a handler bug so one poison message cannot loop.
            self.store.set_seen(message_id, event_time)

    def _dispatch(self, command: Command, message_id: str | None) -> None:
        if command.kind == "help":
            # Signed users get it immediately; unsigned ones share the rejection
            # bucket, so a stranger cannot burn the daily message quota with help.
            if command.signed or self._allow_rate_limited():
                self.notify_text(formatting.USAGE, title="TradingAgents 用法")
            return
        if command.kind == "status":
            self.notify_text(self.status_text(), title="Bridge 状态")
            return
        if command.kind == "resend":
            self.resend(command.job_id or "")
            return
        self._enqueue(command, message_id, bypass=command.kind == "rerun")

    def _allow_rate_limited(self) -> bool:
        """One unauthenticated reply per interval (shared by rejections and help)."""
        now = time.time()
        if now - self._last_reject_reply < REJECTION_REPLY_INTERVAL:
            return False
        self._last_reject_reply = now
        return True

    def _maybe_reject_reply(self) -> None:
        if not self._allow_rate_limited():
            return
        self.notify_text(
            "命令被拒绝：缺少或错误的密钥（10 分钟内只回执一次）",
            title="命令被拒绝",
            tags=("no_entry_sign",),
        )

    # -- job lifecycle -----------------------------------------------------
    def _enqueue(self, command: Command, message_id: str | None, *, bypass: bool) -> None:
        job = {
            "id": new_job_id(),
            "ticker": command.ticker,
            "trade_date": command.trade_date,
            "analysts": list(command.analysts),
            "language": command.language,
            "created_day": date_cls.today().isoformat(),
            "message_id": message_id,
        }
        accepted, reason, position = self.store.add_job(
            job,
            queue_limit=self.config.queue_limit,
            daily_limit=self.config.daily_job_limit,
            bypass_dedupe=bypass,
        )
        if accepted:
            self.notify_text(
                f"已排队（第 {position} 位）：{command.ticker} {command.trade_date}\n"
                f"分析师: {', '.join(command.analysts)}",
                title=f"已接收 {command.ticker} {command.trade_date}",
                tags=("inbox_tray",),
            )
            return
        reasons = {
            "queue_full": f"队列已满（上限 {self.config.queue_limit}），请稍后再发",
            "duplicate": "该标的当天已有任务；需要重跑请用 rerun",
            "budget": f"今日任务已达上限（{self.config.daily_job_limit}）",
        }
        self.notify_text(reasons.get(reason, reason), title="未受理", tags=("no_entry_sign",))

    def resend(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job:
            self.notify_text(f"找不到任务 {job_id}", title="未找到", tags=("no_entry_sign",))
            return
        report = job.get("report_path")
        if not report or not os.path.isfile(report):
            self.notify_text(
                f"任务 {job_id} 的报告已不在本地（保留 {self.config.retention_days} 天）",
                title="无法重发",
                tags=("no_entry_sign",),
            )
            return
        self._publish_report(job, rating=job.get("rating"), decision=job.get("decision"),
                             report_path=report, degraded=bool(job.get("degraded")),
                             dev_build=bool(job.get("dev_build")))

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if time.time() < self._worker_busy_until:
                time.sleep(min(5.0, self._worker_busy_until - time.time()))
                continue
            job = self.store.next_pending()
            if job is None:
                time.sleep(WORKER_IDLE_SLEEP)
                continue
            self._run_job(job)
            if self.config.min_job_gap:
                time.sleep(self.config.min_job_gap)

    def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        self.store.update_job(job_id, status="running", started_ts=time.time())
        self.notify_text(
            f"开始分析 {job['ticker']} {job['trade_date']}",
            title=f"开始 {job['ticker']}",
            tags=("hourglass_flowing_sand",),
        )
        branch, dev_build = git_state(self.config)
        logger.info("job %s on branch %s (dev=%s)", job_id, branch, dev_build)
        result: RunResult = self.runner(job, self.config)

        if result.ok and result.report_path:
            self._finish_success(job, result, dev_build)
        else:
            self._finish_failure(job, result)
        self.store.update_job(job_id, dev_build=dev_build)

    def _finish_success(self, job: dict, result: RunResult, dev_build: bool) -> None:
        published = self._publish_report(
            job,
            rating=result.rating,
            decision=result.decision,
            report_path=result.report_path,
            degraded=result.degraded,
            dev_build=dev_build,
        )
        self.store.update_job(
            job["id"],
            status="done",
            finished_ts=time.time(),
            rating=result.rating,
            decision=(result.decision or "")[:2000],
            report_path=str(result.report_path) if result.report_path else None,
            degraded=result.degraded,
            published=published,
        )

    def _finish_failure(self, job: dict, result: RunResult) -> None:
        self.store.record_failure(job, count_failed=self.config.count_failed)
        tail = scrub(result.log_tail, self._secrets)
        message = (
            f"任务失败：{job['ticker']} {job['trade_date']}\n"
            f"原因：{result.error or '未知错误'}\n"
            f"耗时 {int(result.duration)}s，local job `{job['id']}`\n\n"
            f"```\n{tail}\n```"
        )
        self.notify_text(
            formatting.truncate_encoded(message),
            title=formatting.build_title(job["ticker"], job["trade_date"], "失败"),
            tags=("warning",),
        )
        self.store.update_job(
            job["id"],
            status=FAILED,
            finished_ts=time.time(),
            error=(result.error or "")[:1000],
        )
        if _looks_rate_limited(result.log_tail + " " + (result.error or "")):
            # East Money bans IPs for 5-40 minutes; keep hammering and the ban
            # outlives several queued jobs. Pause this worker only.
            self._worker_busy_until = time.time() + self.config.rate_limit_backoff
            logger.warning("rate-limit failure; pausing bridge jobs for %ss",
                           self.config.rate_limit_backoff)

    # -- publishing --------------------------------------------------------
    def _publish_report(
        self,
        job: dict,
        *,
        rating: str | None,
        decision: str | None,
        report_path: str | None,
        degraded: bool,
        dev_build: bool,
    ) -> bool:
        ticker, trade_date = job["ticker"], job["trade_date"]
        filename = formatting.attachment_name(ticker, trade_date, job["id"])
        title = formatting.build_title(
            ticker, trade_date, rating, prefix=self.config.title_prefix
        )
        tags = [formatting.rating_tag(rating)]
        if degraded:
            tags.append("warning")
        if dev_build:
            tags.append("construction")

        safe_report = self._scrubbed_copy(job, report_path)
        summary = formatting.build_summary(
            ticker=ticker,
            trade_date=trade_date,
            rating=rating,
            decision=decision,
            job_id=job["id"],
            degraded=degraded,
            dev_build=dev_build,
            report_name=filename,
        )
        return self.notify_file(
            safe_report or report_path,
            filename=filename,
            message=summary,
            title=title,
            tags=tuple(tags),
        )

    def _scrubbed_copy(self, job: dict, report_path: str | None) -> str | None:
        """Attach a copy with known secrets removed; the original stays on disk."""
        if not report_path or not os.path.isfile(report_path):
            return None
        try:
            with open(report_path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            return None
        cleaned = scrub(text, self._secrets)
        if cleaned == text:
            return report_path
        target = self.store.job_dir(job["id"]) / "complete_report.scrubbed.md"
        target.write_text(cleaned, encoding="utf-8")
        return str(target)

    # -- notification transport -------------------------------------------
    def _deliver(self, payload: dict) -> bool:
        try:
            self._send(payload)
            return True
        except NtfyError as exc:
            if exc.permanent:
                logger.error("notification dropped permanently: %s", exc)
                self.store.dead_letter(payload, str(exc))
            else:
                logger.warning("notification deferred to outbox: %s", exc)
                self.store.outbox_put(payload)
            return False

    def _send(self, payload: dict) -> None:
        kind = payload.get("kind")
        topic = payload.get("topic") or self.config.result_topic
        tags = tuple(payload.get("tags") or ())
        if kind == "file":
            self.client.publish_file(
                topic,
                payload["path"],
                filename=payload["filename"],
                message=payload.get("message", ""),
                title=payload.get("title"),
                tags=tags,
            )
        else:
            self.client.publish(
                topic,
                payload.get("message", ""),
                title=payload.get("title"),
                tags=tags,
            )

    def notify_text(self, message: str, *, title: str | None = None,
                    tags: tuple[str, ...] = ()) -> bool:
        payload = {
            "kind": "text",
            "topic": self.config.result_topic,
            "message": formatting.truncate_encoded(scrub(message, self._secrets)),
            "title": formatting.clean_title(scrub(title or "", self._secrets)),
            "tags": list(tags),
        }
        return self._deliver(payload)

    def notify_file(self, path: str | Path | None, *, filename: str, message: str,
                    title: str | None = None, tags: tuple[str, ...] = ()) -> bool:
        if not path:
            return self.notify_text(message, title=title, tags=tags)
        payload = {
            "kind": "file",
            "topic": self.config.result_topic,
            "path": str(path),
            "filename": filename,
            "message": formatting.truncate_encoded(scrub(message, self._secrets)),
            "title": formatting.clean_title(scrub(title or "", self._secrets)),
            "tags": list(tags),
        }
        return self._deliver(payload)

    def flush_outbox(self) -> None:
        for path, payload, attempts in self.store.outbox_list():
            try:
                self._send(payload)
                path.unlink(missing_ok=True)
            except NtfyError as exc:
                if exc.permanent or attempts + 1 >= MAX_OUTBOX_ATTEMPTS:
                    self.store.outbox_abandon(path, payload, str(exc))
                else:
                    self.store.outbox_bump(path, payload, attempts + 1)

    # -- periodic work -----------------------------------------------------
    def _notifier_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.flush_outbox()
                self.maybe_heartbeat()
                self._check_listener_health()
                self._prune_daily()
            except Exception:
                logger.exception("notifier iteration failed")
            time.sleep(NOTIFIER_SLEEP)

    def maybe_heartbeat(self) -> None:
        hour = self.config.heartbeat_hour
        if hour < 0 or datetime.now().hour < hour or self.store.heartbeat_sent_today():
            return
        stats = self.store.stats()
        text = (
            "**bridge alive**\n"
            f"今日排队 {stats.get('queued', 0)}，失败 {stats.get('failed', 0)}，"
            f"拒绝 {stats.get('rejected', 0)}\n"
            f"队列 {self.store.queued_count()}，滞留 outbox {len(self.store.outbox_list())}，"
            f"dead-letter {self.store.dead_letter_count_today()}"
        )
        if self.notify_text(text, title="Bridge 每日心跳", tags=("heartbeat",)):
            self.store.mark_heartbeat()

    def _check_listener_health(self) -> None:
        if not self._listener_beat:
            return
        stale = time.time() - self._listener_beat
        if stale <= max(30, 3 * self.config.poll_interval):
            return
        now = time.time()
        if now - self._last_unhealthy_notice < UNHEALTHY_NOTICE_INTERVAL:
            return
        self._last_unhealthy_notice = now
        self.notify_text(
            f"订阅已 {int(stale)}s 未成功轮询，正在重试。",
            title="监听异常",
            tags=("rotating_light",),
        )

    def _prune_daily(self) -> None:
        today = date_cls.today().isoformat()
        if today == self._pruned_day:
            return
        removed = self.store.prune(self.config.retention_days)
        self._pruned_day = today
        if removed:
            logger.info("retention removed %d old entries", removed)

    # -- helpers -----------------------------------------------------------
    def status_text(self) -> str:
        stats = self.store.stats()
        jobs = self.store.load_jobs()
        running = [job for job in jobs if job.get("status") == "running"]
        heartbeat = "已发送" if self.store.heartbeat_sent_today() else "未发送"
        return (
            f"队列 {self.store.queued_count()} / 上限 {self.config.queue_limit}\n"
            f"运行中 {len(running)}\n"
            f"今日：排队 {stats.get('queued', 0)}，失败 {stats.get('failed', 0)}，"
            f"拒绝 {stats.get('rejected', 0)}\n"
            f"outbox {len(self.store.outbox_list())}，"
            f"dead-letter {self.store.dead_letter_count_today()}\n"
            f"心跳 {heartbeat}\n"
            f"已运行 {int(time.time() - self._started_ts)}s"
        )

    def _install_signal_handlers(self) -> None:
        import signal

        def stop(_signum, _frame):
            self._stop.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)


def _looks_signed(text: str, secret: str) -> bool:
    tokens = (text or "").split()
    return bool(tokens) and (tokens[0] == secret or f"secret={secret}" in tokens)


def _looks_rate_limited(text: str) -> bool:
    lowered = (text or "").lower()
    markers = ("限流", "rate limit", "rate-limit", "push2", "429", "too many requests")
    return any(marker in lowered for marker in markers)


def _watchdog_seconds() -> float:
    raw = os.environ.get("WATCHDOG_USEC", "")
    try:
        return float(raw) / 1_000_000 if raw else 0.0
    except ValueError:
        return 0.0


def sd_notify(message: str) -> None:
    """Send a systemd notification without the ``sdnotify`` dependency."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
    except OSError:
        pass
