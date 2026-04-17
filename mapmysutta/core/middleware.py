from __future__ import annotations

import logging

from django.http import HttpRequest
from django.http import JsonResponse
from django.utils import timezone

from mapmysutta.core.models import Device
from mapmysutta.core.security import is_valid_device_id
from mapmysutta.core.security import normalize_device_id

logger = logging.getLogger(__name__)


class DeviceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        path = request.path or ""
        is_v1_api = path.startswith("/api/v1/")
        is_identify_path = path == "/api/v1/device/identify"

        device_id = normalize_device_id(request.headers.get("X-Device-Id"))
        request.device = None
        if not device_id:
            if is_v1_api and not is_identify_path:
                return JsonResponse({"detail": "X-Device-Id header is required."}, status=403)
            return self.get_response(request)

        if not is_valid_device_id(device_id):
            logger.warning("Rejected malformed X-Device-Id header")
            if is_v1_api and not is_identify_path:
                return JsonResponse({"detail": "Invalid X-Device-Id header."}, status=400)
            return self.get_response(request)

        request.device = Device.objects.filter(device_id=device_id).first()
        if request.device is not None:
            request.device.last_active_at = timezone.now()
            request.device.save(update_fields=["last_active_at"])
        elif is_v1_api and not is_identify_path:
            return JsonResponse({"detail": "Device not found. Identify first."}, status=403)
        return self.get_response(request)
