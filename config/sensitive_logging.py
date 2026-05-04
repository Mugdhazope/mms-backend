"""Logging helpers — avoid leaking bearer tokens or device ids into log messages."""

from __future__ import annotations

import logging
import re

_BEARER_RE = re.compile(r"(Bearer\s+)[^\s]+", flags=re.IGNORECASE)
_DEVICE_HEADER_RE = re.compile(r"(X-Device-Id['\":\s]+)[^\s'\"]+", flags=re.IGNORECASE)


class RedactSensitiveAuthFilter(logging.Filter):
    """Best-effort redaction if secrets appear in log message text."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.msg, str):
            return True
        msg = record.msg
        low = msg.lower()
        if "bearer " not in low and "x-device-id" not in low:
            return True
        scrubbed = _BEARER_RE.sub(r"\1<redacted>", msg)
        record.msg = _DEVICE_HEADER_RE.sub(r"\1<redacted>", scrubbed)
        record.args = ()
        return True
