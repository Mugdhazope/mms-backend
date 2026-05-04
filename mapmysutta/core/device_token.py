from __future__ import annotations

from django.conf import settings
from django.core import signing

TOKEN_SALT = "mapmysutta.device-access.v1"  # noqa: S105  # signing salt, not a password


def mint_device_access_token(device_id: str) -> str:
    """Return a signed token bound to device_id (use Authorization: Bearer)."""
    return signing.dumps({"d": device_id}, salt=TOKEN_SALT)


def device_id_from_access_token(token: str) -> str | None:
    """Return device_id if token is valid and not expired; otherwise None."""
    max_age = int(getattr(settings, "DEVICE_ACCESS_TOKEN_MAX_AGE", 30 * 24 * 3600))
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    except signing.SignatureExpired:
        return None
    device_id = payload.get("d")
    if not isinstance(device_id, str):
        return None
    stripped = device_id.strip()
    return stripped or None
