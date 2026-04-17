from __future__ import annotations

import pytest
from django.test import RequestFactory

from mapmysutta.core.middleware import DeviceMiddleware
from mapmysutta.core.tests.factories import DeviceFactory


@pytest.mark.django_db
def test_device_middleware_attaches_device():
    device = DeviceFactory(device_id="abc-device")
    request = RequestFactory().get("/", HTTP_X_DEVICE_ID=device.device_id)

    middleware = DeviceMiddleware(lambda req: req)
    response = middleware(request)

    assert response.device == device


@pytest.mark.django_db
def test_device_middleware_rejects_missing_header_on_v1_api():
    request = RequestFactory().get("/api/v1/spots")
    middleware = DeviceMiddleware(lambda req: req)
    response = middleware(request)
    assert response.status_code == 403


@pytest.mark.django_db
def test_device_middleware_allows_identify_without_header():
    request = RequestFactory().post("/api/v1/device/identify")
    middleware = DeviceMiddleware(lambda req: req)
    response = middleware(request)
    assert response == request
