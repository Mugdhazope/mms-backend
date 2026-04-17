from __future__ import annotations

import re

DEVICE_ID_MAX_LEN = 255
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,255}$")


def normalize_device_id(raw: str | None) -> str:
    return (raw or "").strip()


def is_valid_device_id(raw: str | None) -> bool:
    value = normalize_device_id(raw)
    if not value:
        return False
    if len(value) > DEVICE_ID_MAX_LEN:
        return False
    return bool(DEVICE_ID_RE.fullmatch(value))
