"""Remove credentials from anything that leaves the machine.

Two layers, because either alone is leaky:

* exact-string replacement of the secrets this process actually holds
  (``NTFY_TOKEN``, ``NTFY_COMMAND_SECRET``, ...) - reliable regardless of shape;
* a few patterns for credentials we do not hold (provider keys echoed by a
  traceback, ``Authorization`` values, ``?token=`` in URLs).

Chinese report text must survive untouched: only the listed shapes are masked.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Matches common prefixed API keys. Kept narrow: a generic "long random string"
# rule would eat tickers, hashes and CJK-adjacent text.
_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), "***"),
    (re.compile(r"\btk_[A-Za-z0-9_-]{10,}"), "***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{10,}"), "Bearer ***"),
    (
        re.compile(r"(?i)\b(token|api[_-]?key|apikey|secret|password)=([^&\s\"']{6,})"),
        r"\1=***",
    ),
    (re.compile(r"(?i)\b(authorization|x-api-key)\s*:\s*\S+"), r"\1: ***"),
)


def scrub(text: str | None, secrets: Iterable[str] = ()) -> str:
    """Return ``text`` with known secrets and credential-shaped strings masked."""
    if not text:
        return ""
    result = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, "***")
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
