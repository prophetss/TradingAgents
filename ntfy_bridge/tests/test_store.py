from __future__ import annotations

import json
import time

from ntfy_bridge.jobs import DONE, Store, new_job_id


def _job(ticker="600519.SH", when="2026-11-20"):
    return {
        "id": new_job_id(),
        "ticker": ticker,
        "trade_date": when,
        "analysts": ["market"],
        "created_day": time.strftime("%Y-%m-%d"),
    }


def _add(store, job, *, queue_limit=10, daily_limit=10, bypass=False):
    return store.add_job(job, queue_limit=queue_limit, daily_limit=daily_limit,
                         bypass_dedupe=bypass)


def test_job_is_persisted_and_deduped_for_the_day(tmp_path):
    store = Store(tmp_path)
    job = _job()
    assert _add(store, job)[0] is True
    accepted, reason, _pos = _add(store, _job())
    assert accepted is False and reason == "duplicate"
    # A different ticker is not a duplicate.
    assert _add(store, _job(ticker="000001.SZ"))[0] is True
    # reopen: the queue survives a restart
    assert len(Store(tmp_path).load_jobs()) == 2


def test_rerun_bypasses_the_dedupe(tmp_path):
    store = Store(tmp_path)
    _add(store, _job())
    assert _add(store, _job(), bypass=True)[0] is True


def test_queue_and_daily_limits(tmp_path):
    store = Store(tmp_path)
    assert _add(store, _job(), queue_limit=2)[0] is True
    assert _add(store, _job(ticker="AAPL"), queue_limit=2)[0] is True
    ok, reason, _ = _add(store, _job(ticker="MSFT"), queue_limit=2)
    assert (ok, reason) == (False, "queue_full")
    # Daily budget counts accepted jobs, not the queue depth.
    store2 = Store(tmp_path / "second")
    for index in range(2):
        assert _add(store2, _job(ticker=f"T{index}"), daily_limit=2, queue_limit=10)[0]
    ok, reason, _ = _add(store2, _job(ticker="T9"), daily_limit=2, queue_limit=10)
    assert (ok, reason) == (False, "budget")


def test_next_pending_marks_running_once(tmp_path):
    store = Store(tmp_path)
    job = _job()
    _add(store, job)
    first = store.next_pending()
    assert first and first["id"] == job["id"] and first["status"] == "running"
    assert store.next_pending() is None
    store.update_job(job["id"], status=DONE)
    assert store.active_count() == 0


def test_seen_roundtrip(tmp_path):
    store = Store(tmp_path)
    assert store.last_seen() is None
    store.set_seen("abc123", 1234.0)
    assert store.last_seen() == "abc123"
    assert store.last_seen_ts() == 1234.0


def test_corrupt_state_is_set_aside_not_fatal(tmp_path):
    store = Store(tmp_path)
    store.queue_file.write_text("{not json", encoding="utf-8")
    assert store.load_jobs() == []
    assert list(tmp_path.glob("queue.json.corrupt-*"))


def test_outbox_bump_and_abandon(tmp_path):
    store = Store(tmp_path)
    payload = {"kind": "text", "message": "x"}
    path = store.outbox_put(payload)
    assert len(store.outbox_list()) == 1
    store.outbox_bump(path, payload, 2)
    entries = store.outbox_list()
    assert entries[0][1] == payload and entries[0][2] == 2
    store.outbox_abandon(path, payload, "400")
    assert store.outbox_list() == []
    assert store.dead_letter_count_today() == 1
    saved = json.loads(next(store.dead_letter_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["reason"] == "400"


def test_prune_removes_old_job_dirs(tmp_path):
    store = Store(tmp_path)
    job = _job()
    _add(store, job)
    old = store.job_dir(job["id"])
    (old / "log.txt").write_text("x", encoding="utf-8")
    past = time.time() - 30 * 86400
    import os

    os.utime(old, (past, past))
    assert store.prune(14) >= 1
    assert not old.exists()
