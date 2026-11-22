"""Thin ntfy HTTP client (requests only).

Message and title travel as URL query parameters, not headers: ``requests``
encodes header values as latin-1 and the summaries are usually Chinese. Tags
are ASCII emoji shortcodes for the same reason.

Error classification is what drives retry behaviour:
    permanent  -> 4xx except 408/429 (bad request, auth, too large)
    retryable  -> network errors, 408, 429, 5xx
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

from .config import TOPIC_RE

logger = logging.getLogger(__name__)

USER_AGENT = "tradingagents-ntfy-bridge/1.0"


class NtfyError(RuntimeError):
    """A publish/subscribe request failed."""

    def __init__(self, message: str, *, status: int | None = None, permanent: bool = False):
        super().__init__(message)
        self.status = status
        self.permanent = permanent


def _classify(status: int) -> bool:
    """Whether a non-2xx HTTP status is permanent (True) or worth retrying."""
    if status in (408, 429):
        return False
    return 400 <= status < 500


class NtfyClient:
    def __init__(
        self,
        server: str,
        token: str | None = None,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 30.0),
        stream_read_timeout: float = 90.0,
    ):
        self.server = server.rstrip("/")
        self.token = token
        self.session = session or requests.Session()
        self.timeout = timeout
        self.stream_read_timeout = stream_read_timeout

    # -- internals ---------------------------------------------------------
    def _url(self, topic: str) -> str:
        if not TOPIC_RE.match(topic or ""):
            raise NtfyError(f"非法 topic: {topic!r}", permanent=True)
        return f"{self.server}/{topic}"

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _check(self, response: requests.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        body = (response.text or "")[:300]
        raise NtfyError(
            f"ntfy HTTP {response.status_code}: {body}",
            status=response.status_code,
            permanent=_classify(response.status_code),
        )

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise NtfyError(f"ntfy 请求失败: {type(exc).__name__}: {exc}") from exc
        self._check(response)
        return response

    @staticmethod
    def _payload(**params) -> dict[str, str]:
        return {key: value for key, value in params.items() if value not in (None, "", ())}

    # -- publishing --------------------------------------------------------
    def publish(
        self,
        topic: str,
        message: str,
        *,
        title: str | None = None,
        tags: tuple[str, ...] = (),
        priority: int | None = None,
        markdown: bool = True,
    ) -> dict:
        """POST a text notification; the body is the message."""
        params = self._payload(
            title=title,
            tags=",".join(tags) if tags else None,
            priority=str(priority) if priority else None,
        )
        headers = self._headers({"Markdown": "yes"} if markdown else None)
        response = self._request(
            "POST",
            self._url(topic),
            params=params,
            headers=headers,
            data=message.encode("utf-8"),
        )
        return _json_or_empty(response)

    def publish_file(
        self,
        topic: str,
        path: str | Path,
        *,
        filename: str,
        message: str = "",
        title: str | None = None,
        tags: tuple[str, ...] = (),
        priority: int | None = None,
    ) -> dict:
        """PUT a local file as an attachment, with the summary as the message."""
        source = Path(path)
        if not source.is_file():
            raise NtfyError(f"附件不存在: {source}", permanent=True)
        params = self._payload(
            message=message or None,
            title=title,
            tags=",".join(tags) if tags else None,
            priority=str(priority) if priority else None,
        )
        headers = self._headers(
            {"Filename": filename, "Content-Type": "text/markdown; charset=utf-8"}
        )
        response = self._request(
            "PUT",
            self._url(topic),
            params=params,
            headers=headers,
            data=source.read_bytes(),
        )
        return _json_or_empty(response)

    # -- subscribing -------------------------------------------------------
    def poll(self, topic: str, *, since: str | None = None, timeout: float | None = None) -> list[dict]:
        """Read cached messages once and return them."""
        params = {"poll": "1"}
        if since:
            params["since"] = since
        try:
            response = self.session.get(
                self._url(topic) + "/json",
                params=params,
                headers=self._headers(),
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise NtfyError(f"ntfy 订阅失败: {type(exc).__name__}: {exc}") from exc
        self._check(response)
        return _messages(response.text)

def _json_or_empty(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError:
        return {}


def _messages(text: str) -> list[dict]:
    events: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
