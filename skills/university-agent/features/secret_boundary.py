"""Model/log-safe views for values that may cross a local output boundary."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_SECRET_KEY = re.compile(
    r"(?:password|passwd|pwd|secret|token|cookie|authorization|"
    r"moodlesession|sesskey|sessionid)", re.IGNORECASE,
)
_SECRET_TEXT = (
    re.compile(r"(?i)(\b(?:moodlesession|sesskey)\s*=\s*)[^;\s,]+"),
    re.compile(r"(?i)(\b(?:cookie|set-cookie|authorization)\s*:\s*)[^\r\n]+"),
    re.compile(r"(?i)(\b(?:authorization)\s+bearer\s+)[^\s,]+"),
    re.compile(r"(?i)([?&](?:password|passwd|pwd|secret|token|access_token|refresh_token|sesskey)=[^&#\s]+)"),
)


def _safe_text(value: str) -> str:
    for pattern in _SECRET_TEXT:
        value = pattern.sub(lambda match: match.group(1) + "[REDACTED]", value)
    return value


def redact_output(value: Any, *, _key: str = "") -> Any:
    """Copy a result while removing secret fields and credential-shaped text."""
    if _key and _SECRET_KEY.search(_key.replace("_", "").replace("-", "")):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(key): redact_output(item, _key=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_output(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_output(item) for item in value)
    if isinstance(value, str):
        return _safe_text(value)
    return value


__all__ = ["redact_output"]
