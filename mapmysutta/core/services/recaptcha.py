from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecaptchaCheckResult:
    passed: bool
    requires_manual_challenge: bool
    reason: str


def _verify_token(token: str, remote_ip: str | None) -> dict[str, object]:
    payload = {
        "secret": settings.RECAPTCHA_SECRET_KEY,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip
    encoded = urlencode(payload).encode("utf-8")
    req = Request(settings.RECAPTCHA_VERIFY_URL, data=encoded, method="POST")
    with urlopen(req, timeout=4) as res:  # noqa: S310 - fixed allowlisted URL from settings
        body = res.read().decode("utf-8")
    return json.loads(body)


def verify_recaptcha_v3(*, token: str, action: str, remote_ip: str | None) -> RecaptchaCheckResult:
    if not settings.RECAPTCHA_ENFORCED:
        return RecaptchaCheckResult(True, False, "disabled")
    if not token.strip():
        return RecaptchaCheckResult(False, True, "missing-token")
    try:
        data = _verify_token(token=token.strip(), remote_ip=remote_ip)
    except Exception:  # noqa: BLE001
        logger.exception("reCAPTCHA verification request failed")
        return RecaptchaCheckResult(False, True, "verify-request-failed")

    if not data.get("success", False):
        reason = "invalid-token"
        error_codes = data.get("error-codes")
        if isinstance(error_codes, list) and error_codes:
            reason = f"invalid-token:{','.join(str(x) for x in error_codes)}"
        return RecaptchaCheckResult(False, True, reason)

    hostname = str(data.get("hostname", "")).strip().lower()
    if settings.RECAPTCHA_ALLOWED_HOSTNAMES and hostname not in settings.RECAPTCHA_ALLOWED_HOSTNAMES:
        return RecaptchaCheckResult(False, False, "hostname-mismatch")

    response_action = str(data.get("action", "")).strip()
    if response_action and response_action != action:
        return RecaptchaCheckResult(False, False, "action-mismatch")

    score_raw = data.get("score")
    score = float(score_raw) if isinstance(score_raw, int | float) else 0.0
    if score < settings.RECAPTCHA_SCORE_THRESHOLD:
        return RecaptchaCheckResult(False, True, f"low-score:{score:.2f}")

    return RecaptchaCheckResult(True, False, "ok")


def verify_recaptcha_manual(*, token: str, remote_ip: str | None) -> RecaptchaCheckResult:
    if not settings.RECAPTCHA_ENFORCED:
        return RecaptchaCheckResult(True, False, "disabled")
    if not token.strip():
        return RecaptchaCheckResult(False, False, "missing-manual-token")
    try:
        data = _verify_token(token=token.strip(), remote_ip=remote_ip)
    except Exception:  # noqa: BLE001
        logger.exception("manual reCAPTCHA verification request failed")
        return RecaptchaCheckResult(False, False, "verify-request-failed")

    if not data.get("success", False):
        return RecaptchaCheckResult(False, False, "manual-invalid-token")

    hostname = str(data.get("hostname", "")).strip().lower()
    if settings.RECAPTCHA_ALLOWED_HOSTNAMES and hostname not in settings.RECAPTCHA_ALLOWED_HOSTNAMES:
        return RecaptchaCheckResult(False, False, "hostname-mismatch")

    return RecaptchaCheckResult(True, False, "manual-ok")
