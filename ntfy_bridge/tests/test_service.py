from __future__ import annotations

import time

from ntfy_bridge.client import NtfyError
from ntfy_bridge.config import Config
from ntfy_bridge.runner import RunResult
from ntfy_bridge.service import Service


class FakeClient:
    def __init__(self):
        self.texts: list[dict] = []
        self.files: list[dict] = []
        self.events: list[dict] = []
        self.fail: Exception | None = None

    def publish(self, topic, message, **kwargs):
        if self.fail:
            raise self.fail
        self.texts.append({"topic": topic, "message": message, **kwargs})
        return {}

    def publish_file(self, topic, path, **kwargs):
        if self.fail:
            raise self.fail
        self.files.append({"topic": topic, "path": path, **kwargs})
        return {}

    def poll(self, topic, since=None, timeout=None):
        events, self.events = self.events, []
        return events

    def stream(self, topic, since=None):
        return iter(())


def _config(tmp_path, *, secret=None):
    return Config(
        server="https://ntfy.example",
        command_topic="cmd",
        result_topic="result",
        command_secret=secret,
        home=tmp_path / "home",
        repo_root=tmp_path,
        heartbeat_hour=-1,
        min_job_gap=0,
        job_timeout=60,
    )


def _service(tmp_path, runner, *, secret=None):
    config = _config(tmp_path, secret=secret)
    client = FakeClient()
    return Service(config, client=client, runner=runner), client


def _event(message, message_id="m1"):
    return {"event": "message", "id": message_id, "time": time.time(), "message": message}


def _runner_with_report(tmp_path, *, ok=True):
    report = tmp_path / "complete_report.md"
    report.write_text("# 报告\n正文", encoding="utf-8")

    def runner(job, config):
        if ok:
            return RunResult(
                job_id=job["id"], exit_code=0, timed_out=False, duration=1.5,
                rating="Overweight", decision="建议加仓，注意止损。",
                report_path=report, degraded=False, log_path=None, log_tail="",
            )
        return RunResult(
            job_id=job["id"], exit_code=1, timed_out=False, duration=2.0,
            report_path=None, log_path=None, log_tail="push2 限流", error="CLI 退出码 1",
        )

    return runner


def test_command_becomes_job_then_attachment(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path))

    service._handle_event(_event("600519.SH 2026-11-20 market"))
    assert len(client.texts) == 1 and "已排队" in client.texts[0]["message"]

    service.run_once()

    assert len(client.files) == 1
    published = client.files[0]
    assert published["filename"].endswith(".md")
    assert "Overweight" in published["message"]
    assert "600519.SH" in published["title"]
    job = service.store.load_jobs()[0]
    assert job["status"] == "done"
    assert job["rating"] == "Overweight"


def test_unsigned_command_gets_one_rejection_then_silence(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path), secret="s3cret")

    service._handle_event(_event("600519.SH", "m1"))
    service._handle_event(_event("000001.SZ", "m2"))

    assert len(client.texts) == 1
    assert "拒绝" in client.texts[0]["message"]
    assert service.store.stats()["rejected"] == 2
    assert service.store.load_jobs() == []


def test_unsigned_help_is_rate_limited(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path), secret="s3cret")

    service._handle_event(_event("help", "m1"))
    service._handle_event(_event("help", "m2"))

    assert len(client.texts) == 1
    assert "可用命令" in client.texts[0]["message"]


def test_failure_publishes_warning_and_pauses_on_rate_limit(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path, ok=False))

    service._handle_event(_event("600519.SH 2026-11-20", "m1"))
    service.run_once()

    failure = client.texts[-1]
    assert "任务失败" in failure["message"]
    assert failure["tags"] == ("warning",)
    assert service.store.load_jobs()[0]["status"] == "failed"
    assert service._worker_busy_until > time.time()


def test_transient_publish_failure_goes_to_outbox_and_flushes(tmp_path):
    runner = _runner_with_report(tmp_path)
    service, client = _service(tmp_path, runner)
    client.fail = NtfyError("network down", permanent=False)

    service._handle_event(_event("600519.SH 2026-11-20", "m1"))
    assert len(service.store.outbox_list()) == 1

    client.fail = None
    service.flush_outbox()

    assert service.store.outbox_list() == []
    assert any("已排队" in entry["message"] for entry in client.texts)


def test_permanent_publish_failure_goes_to_dead_letter(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path))
    client.fail = NtfyError("bad request", status=400, permanent=True)

    service._handle_event(_event("600519.SH 2026-11-20", "m1"))

    assert service.store.outbox_list() == []
    assert service.store.dead_letter_count_today() == 1


def test_resend_re_publishes_a_stored_report(tmp_path):
    runner = _runner_with_report(tmp_path)
    service, client = _service(tmp_path, runner)
    service._handle_event(_event("600519.SH 2026-11-20", "m1"))
    service.run_once()
    job = service.store.load_jobs()[0]

    service.resend(job["id"])

    assert len(client.files) == 2


def test_long_downtime_is_reported_once(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path))
    service.store.set_seen("old-id", time.time() - 13 * 3600)

    service._poll_once()
    service._poll_once()

    notices = [entry for entry in client.texts if "停机" in entry["message"]]
    assert len(notices) == 1
    assert "12 小时" in notices[0]["message"]


def test_duplicate_command_is_rejected(tmp_path):
    service, client = _service(tmp_path, _runner_with_report(tmp_path))

    service._handle_event(_event("600519.SH 2026-11-20", "m1"))
    service._handle_event(_event("600519.SH 2026-11-20", "m2"))

    assert any("已有任务" in entry["message"] for entry in client.texts)
