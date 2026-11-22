from __future__ import annotations

import json

import pytest
import requests

from ntfy_bridge.client import NtfyClient, NtfyError


class FakeResponse:
    def __init__(self, status=200, text="", payload=None, lines=None):
        self.status_code = status
        self.text = text
        self._payload = payload
        self._lines = lines or []
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def iter_lines(self):
        yield from self._lines

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def _next(self):
        return self.responses.pop(0) if self.responses else FakeResponse()

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._next()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()


def _client(responses=None, token="tk_testtoken123456"):
    session = FakeSession(responses)
    return NtfyClient("https://ntfy.example", token, session=session), session


def test_publish_puts_message_in_body_and_title_in_query():
    client, session = _client([FakeResponse(payload={"id": "x"})])

    client.publish("result", "中文正文", title="标题", tags=("warning",))

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://ntfy.example/result"
    assert kwargs["data"] == "中文正文".encode()
    assert kwargs["params"] == {"title": "标题", "tags": "warning"}
    assert kwargs["headers"]["Authorization"] == "Bearer tk_testtoken123456"
    assert kwargs["headers"]["Markdown"] == "yes"


def test_publish_file_uses_query_params_for_unicode(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# 报告", encoding="utf-8")
    client, session = _client()

    client.publish_file(
        "result", report, filename="600519.SH_2026-11-20_ab.md",
        message="完成", title="600519.SH · Hold", tags=("chart_with_upwards_trend",),
    )

    method, url, kwargs = session.calls[0]
    assert method == "PUT"
    assert kwargs["params"]["message"] == "完成"
    assert kwargs["params"]["title"] == "600519.SH · Hold"
    assert kwargs["headers"]["Filename"] == "600519.SH_2026-11-20_ab.md"
    assert kwargs["data"] == "# 报告".encode()


@pytest.mark.parametrize(
    "status,permanent",
    [(400, True), (401, True), (413, True), (408, False), (429, False), (500, False)],
)
def test_error_classification_drives_retry(status, permanent):
    client, _session = _client([FakeResponse(status=status, text="nope")])
    with pytest.raises(NtfyError) as error:
        client.publish("result", "x")
    assert error.value.permanent is permanent


def test_network_error_is_retryable():
    class Exploding(FakeSession):
        def request(self, method, url, **kwargs):
            raise requests.ConnectionError("down")

    client = NtfyClient("https://ntfy.example", session=Exploding())
    with pytest.raises(NtfyError) as error:
        client.publish("result", "x")
    assert error.value.permanent is False


def test_poll_parses_ndjson():
    body = "\n".join(
        [
            json.dumps({"event": "open"}),
            json.dumps({"event": "message", "id": "a", "message": "600519.SH"}),
        ]
    )
    client, session = _client([FakeResponse(text=body)])
    events = client.poll("cmd", since="prev")
    assert session.calls[0][1].endswith("/cmd/json")
    assert [event["event"] for event in events] == ["open", "message"]


def test_invalid_topic_is_refused():
    client, _session = _client()
    with pytest.raises(NtfyError):
        client.publish("bad topic!", "x")
