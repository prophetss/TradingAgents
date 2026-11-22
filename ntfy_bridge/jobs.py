"""Persistent job queue and small state files.

Every mutation goes through one lock and one atomic write (temp file + fsync +
``os.replace``), so a power cut leaves either the old file or the new one, never
a torn one. The queue is written before the sender is acknowledged, and the
seen-message id after enqueue; on the next start, replay makes redelivery a
no-op because the on-disk (ticker, date) dedupe already knows the job.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

ACTIVE_STATUSES = (QUEUED, RUNNING)


def _today() -> str:
    return date_cls.today().isoformat()


def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("%s is unreadable (%s); starting from defaults", path, exc)
        with contextlib.suppress(OSError):
            path.replace(path.with_name(path.name + f".corrupt-{int(time.time())}"))
        return default


class Store:
    """Queue, counters, outbox and dead-letter files under ``home``."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.lock = threading.RLock()
        for directory in (self.home, self.jobs_dir, self.outbox_dir, self.dead_letter_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------
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

    def job_dir(self, job_id: str) -> Path:
        directory = self.jobs_dir / job_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    # -- queue -------------------------------------------------------------
    def load_jobs(self) -> list[dict]:
        with self.lock:
            data = _read_json(self.queue_file, {"jobs": []})
            jobs = data.get("jobs") if isinstance(data, dict) else None
            return jobs if isinstance(jobs, list) else []

    def _save_jobs(self, jobs: list[dict]) -> None:
        _atomic_write_json(self.queue_file, {"jobs": jobs})

    def get_job(self, job_id: str) -> dict | None:
        return next((job for job in self.load_jobs() if job.get("id") == job_id), None)

    def update_job(self, job_id: str, **fields) -> dict | None:
        with self.lock:
            jobs = self.load_jobs()
            for job in jobs:
                if job.get("id") == job_id:
                    job.update(fields)
                    self._save_jobs(jobs)
                    return job
        return None

    def active_count(self) -> int:
        return sum(1 for job in self.load_jobs() if job.get("status") in ACTIVE_STATUSES)

    def next_pending(self) -> dict | None:
        with self.lock:
            jobs = self.load_jobs()
            pending = [job for job in jobs if job.get("status") == QUEUED]
            if not pending:
                return None
            pending.sort(key=lambda job: job.get("created_ts", 0))
            job = pending[0]
            job["status"] = RUNNING
            job["started_ts"] = time.time()
            self._save_jobs(jobs)
            return dict(job)

    def add_job(self, job: dict, *, queue_limit: int, daily_limit: int,
                bypass_dedupe: bool = False) -> tuple[bool, str, int]:
        """Queue a job; return (accepted, reason, queue position)."""
        with self.lock:
            jobs = self.load_jobs()
            active = [j for j in jobs if j.get("status") in ACTIVE_STATUSES]
            if len(active) >= queue_limit:
                return False, "queue_full", 0
            if not bypass_dedupe:
                for existing in jobs:
                    if (
                        existing.get("ticker") == job["ticker"]
                        and existing.get("trade_date") == job["trade_date"]
                        and existing.get("created_day") == job.get("created_day")
                        and existing.get("status") in (QUEUED, RUNNING, DONE)
                    ):
                        return False, "duplicate", 0
            stats = self._stats()
            if daily_limit and stats.get("queued", 0) >= daily_limit:
                return False, "budget", 0
            job = dict(job)
            job.setdefault("created_ts", time.time())
            job["status"] = QUEUED
            jobs.append(job)
            self._save_jobs(jobs)
            stats["queued"] = stats.get("queued", 0) + 1
            self._save_stats(stats)
            return True, "queued", len(active) + 1

    # -- seen / stats ------------------------------------------------------
    def last_seen(self) -> str | None:
        data = _read_json(self.seen_file, {})
        value = data.get("last_id")
        return value if isinstance(value, str) and value else None

    def last_seen_ts(self) -> float | None:
        data = _read_json(self.seen_file, {})
        value = data.get("last_ts")
        return float(value) if isinstance(value, (int, float)) else None

    def set_seen(self, message_id: str | None, event_time: float | None = None) -> None:
        if not message_id:
            return
        with self.lock:
            _atomic_write_json(
                self.seen_file,
                {"last_id": message_id, "last_ts": float(event_time or time.time())},
            )

    def _stats(self) -> dict:
        data = _read_json(self.stats_file, {})
        if not isinstance(data, dict):
            data = {}
        if data.get("day") != _today():
            # Keep the heartbeat marker across the rollover; reset counters.
            data = {"day": _today(), "queued": 0, "failed": 0, "rejected": 0,
                    "heartbeat_day": data.get("heartbeat_day")}
        return data

    def _save_stats(self, data: dict) -> None:
        _atomic_write_json(self.stats_file, data)

    def stats(self) -> dict:
        with self.lock:
            return dict(self._stats())

    def record_failure(self, job: dict, *, count_failed: bool) -> None:
        with self.lock:
            stats = self._stats()
            stats["failed"] = stats.get("failed", 0) + 1
            if not count_failed:
                stats["queued"] = max(0, stats.get("queued", 0) - 1)
            self._save_stats(stats)

    def record_rejected(self) -> None:
        with self.lock:
            stats = self._stats()
            stats["rejected"] = stats.get("rejected", 0) + 1
            self._save_stats(stats)

    def heartbeat_sent_today(self) -> bool:
        with self.lock:
            return self._stats().get("heartbeat_day") == _today()

    def mark_heartbeat(self) -> None:
        with self.lock:
            stats = self._stats()
            stats["heartbeat_day"] = _today()
            self._save_stats(stats)

    # -- outbox / dead letters --------------------------------------------
    def outbox_put(self, payload: dict) -> Path:
        with self.lock:
            path = self.outbox_dir / f"{time.time_ns()}.json"
            _atomic_write_json(path, {"attempts": 0, "payload": payload})
            return path

    def outbox_list(self) -> list[tuple[Path, dict, int]]:
        entries = []
        for path in sorted(self.outbox_dir.glob("*.json")):
            data = _read_json(path, {})
            if isinstance(data, dict) and isinstance(data.get("payload"), dict):
                entries.append((path, data["payload"], int(data.get("attempts", 0))))
        return entries

    def outbox_bump(self, path: Path, payload: dict, attempts: int) -> None:
        """Persist a retry count so a crash does not reset the attempt budget."""
        _atomic_write_json(path, {"attempts": attempts, "payload": payload})

    def outbox_abandon(self, path: Path, payload: dict, reason: str) -> None:
        self.dead_letter(payload, reason)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not remove outbox entry %s: %s", path, exc)

    def dead_letter(self, payload: dict, reason: str) -> Path:
        with self.lock:
            path = self.dead_letter_dir / f"{time.time_ns()}.json"
            _atomic_write_json(
                path,
                {"at": datetime.now().isoformat(timespec="seconds"),
                 "reason": reason, "payload": payload},
            )
            return path

    def dead_letter_count_today(self) -> int:
        prefix = _today()
        return sum(
            1
            for path in self.dead_letter_dir.glob("*.json")
            if datetime.fromtimestamp(path.stat().st_mtime).date().isoformat() == prefix
        )

    # -- retention ---------------------------------------------------------
    def prune(self, retention_days: int, now: float | None = None) -> int:
        """Remove job directories and dead letters older than the retention."""
        if retention_days <= 0:
            return 0
        cutoff = (now or time.time()) - retention_days * 86400
        removed = 0
        for path in self.jobs_dir.iterdir():
            if path.is_dir() and path.stat().st_mtime < cutoff:
                _remove_tree(path)
                removed += 1
        for path in self.dead_letter_dir.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def queued_count(self) -> int:
        return sum(1 for job in self.load_jobs() if job.get("status") == QUEUED)


def _remove_tree(path: Path) -> None:
    for child in path.rglob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
    path.rmdir()


def new_job_id(now: datetime | None = None) -> str:
    """Sortable, filesystem-safe id: timestamp plus a short random suffix."""
    now = now or datetime.now()
    suffix = os.urandom(3).hex()
    return now.strftime("%Y%m%d-%H%M%S") + "-" + suffix


def prune_hint(retention_days: int) -> str:
    return f"任务目录保留 {timedelta(days=retention_days)}"
