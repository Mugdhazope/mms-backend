from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from mapmysutta.core.device_token import mint_device_access_token
from mapmysutta.core.limits import NOTE_TEXT_MAX_LENGTH
from mapmysutta.core.limits import SPOT_LIST_MAX_RADIUS_M
from mapmysutta.core.models import Device
from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotNote
from mapmysutta.core.models import SpotTag
from mapmysutta.core.models import SpotUsualTiming
from mapmysutta.core.tests.factories import SpotFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def captcha_enforced(settings, monkeypatch):
    settings.RECAPTCHA_ENFORCED = True
    settings.RECAPTCHA_SECRET_KEY = "test-secret"
    settings.RECAPTCHA_ALLOWED_HOSTNAMES = set()
    from mapmysutta.core.api import views
    from mapmysutta.core.services.recaptcha import RecaptchaCheckResult

    def _allow_v3(*, token: str, action: str, remote_ip: str | None):  # noqa: ARG001
        if token == "manual-needed":
            return RecaptchaCheckResult(False, True, "low-score:0.1")
        if token.strip():
            return RecaptchaCheckResult(True, False, "ok")
        return RecaptchaCheckResult(False, True, "missing-token")

    def _allow_manual(*, token: str, remote_ip: str | None):  # noqa: ARG001
        if token == "manual-pass":
            return RecaptchaCheckResult(True, False, "manual-ok")
        return RecaptchaCheckResult(False, False, "manual-invalid-token")

    monkeypatch.setattr(views, "verify_recaptcha_v3", _allow_v3)
    monkeypatch.setattr(views, "verify_recaptcha_manual", _allow_manual)


@pytest.mark.django_db
def test_device_identify_is_idempotent_and_username_immutable(api_client: APIClient):
    url = "/api/v1/device/identify"
    first = api_client.post(url, {"device_id": "device-123", "username": "mz"}, format="json")
    second = api_client.post(url, {"device_id": "device-123", "username": "othername"}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert Device.objects.filter(device_id="device-123").count() == 1
    assert second.data["username"] == "mz"
    assert isinstance(first.data.get("access_token"), str)
    assert first.data["access_token"]


@pytest.mark.django_db
def test_device_identify_rejects_taken_username(api_client: APIClient):
    Device.objects.create(device_id="seed-device-01", username="mz")
    response = api_client.post(
        "/api/v1/device/identify",
        {"device_id": "seed-device-02", "username": "MZ"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "username_taken"


@pytest.mark.django_db
def test_device_detail_returns_karma(api_client: APIClient):
    device = Device.objects.create(device_id="detail-me", username="detail_me", karma=7, trust_score=3.5)
    url = "/api/v1/auth/device"
    api_client.credentials(HTTP_X_DEVICE_ID=device.device_id)
    response = api_client.get(url)
    assert response.status_code == 200
    assert response.data["device_id"] == "detail-me"
    assert response.data["karma"] == 7
    assert response.data["trust_score"] == 3.5
    assert isinstance(response.data.get("access_token"), str)
    assert response.data["access_token"]


@pytest.mark.django_db
def test_spot_list_include_mine_outside_radius(api_client: APIClient):
    """Search-placed pins stay visible even when GPS is far from the pin."""
    spot = SpotFactory(latitude=19.01, longitude=72.83)
    url = (
        "/api/v1/spots?lat=19.97&lng=73.79&radiusM=6500&includeMine=true"
    )
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    response = api_client.get(url)
    assert response.status_code == 200
    ids = {r["id"] for r in response.data["results"]}
    assert str(spot.id) in ids


@pytest.mark.django_db
def test_spot_list_includes_tags_and_notes(api_client: APIClient):
    spot = SpotFactory()
    SpotTag.objects.create(spot=spot, tag="open_late")
    SpotNote.objects.create(spot=spot, device=spot.created_by, text="Great place")
    url = "/api/v1/spots"
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    response = api_client.get(url)
    assert response.status_code == 200
    row = next(r for r in response.data["results"] if r["id"] == str(spot.id))
    assert "open_late" in row["tags"]
    note_texts = [item["text"] for item in row["notes"]]
    assert "Great place" in note_texts
    great = next(item for item in row["notes"] if item["text"] == "Great place")
    assert great.get("created_at") is not None


@pytest.mark.django_db
def test_spot_tag_append_adds_tag_and_returns_row(api_client: APIClient):
    spot = SpotFactory()
    device_id = spot.created_by.device_id
    api_client.credentials(HTTP_X_DEVICE_ID=device_id)
    url = f"/api/v1/spots/{spot.id}/tags"
    before_karma = spot.created_by.karma
    response = api_client.post(url, {"tag": "cash_only"}, format="json")
    assert response.status_code == 200
    assert "cash_only" in response.data["tags"]
    spot.created_by.refresh_from_db()
    assert spot.created_by.karma == before_karma + 2
    dup = api_client.post(url, {"tag": "cash_only"}, format="json")
    assert dup.status_code == 200
    assert dup.data["tags"].count("cash_only") == 1
    spot.created_by.refresh_from_db()
    assert spot.created_by.karma == before_karma + 2


@pytest.mark.django_db
def test_spot_timing_upsert_and_cooldown(api_client: APIClient):
    spot = SpotFactory()
    spot.created_by.karma = 5
    spot.created_by.save(update_fields=["karma"])
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    url = f"/api/v1/spots/{spot.id}/timing"

    first = api_client.post(url, {"usual_open_until": "04:00"}, format="json")
    second = api_client.post(url, {"usual_open_until": "03:30"}, format="json")

    assert first.status_code == 200
    assert first.data["usual_open_until"] is not None
    assert "timing_confidence" in first.data
    assert "timing_label" in first.data
    assert "is_likely_open_now" in first.data
    assert second.status_code == 200
    assert second.data.get("timing_update_accepted") is False
    row = SpotUsualTiming.objects.get(spot=spot, device=spot.created_by)
    assert row.usual_open_until.hour == 4 and row.usual_open_until.minute == 0


@pytest.mark.django_db
def test_spot_timing_same_value_within_cooldown_is_idempotent(api_client: APIClient):
    """Duplicate POST (double-tap) with the same time should not 429 or double karma."""
    spot = SpotFactory()
    spot.created_by.karma = 5
    spot.created_by.save(update_fields=["karma"])
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    url = f"/api/v1/spots/{spot.id}/timing"

    first = api_client.post(url, {"usual_open_until": "04:00"}, format="json")
    second = api_client.post(url, {"usual_open_until": "04:00"}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    spot.created_by.refresh_from_db()
    assert spot.created_by.karma == 7
    assert SpotUsualTiming.objects.filter(spot=spot, device=spot.created_by).count() == 1


@pytest.mark.django_db
def test_spot_timing_rejects_daytime_values(api_client: APIClient):
    spot = SpotFactory()
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    url = f"/api/v1/spots/{spot.id}/timing"

    response = api_client.post(url, {"usual_open_until": "14:00"}, format="json")

    assert response.status_code == 400
    assert "usual_open_until" in response.data


@pytest.mark.django_db
def test_spot_list_includes_timing_aggregate_fields(api_client: APIClient):
    spot = SpotFactory()
    SpotUsualTiming.objects.create(
        spot=spot,
        device=spot.created_by,
        usual_open_until="04:00",
        weight=1.0,
    )
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)

    response = api_client.get("/api/v1/spots")

    assert response.status_code == 200
    row = next(r for r in response.data["results"] if r["id"] == str(spot.id))
    assert row["usual_open_until"] == "04:00"
    assert isinstance(row["timing_confidence"], float)
    assert row["timing_label"] in {"RUNS_LATE", "NORMAL", "UNPREDICTABLE", None}
    assert isinstance(row["is_likely_open_now"], bool)
    assert "typical_close_time" in row
    assert isinstance(row["open_probability"], float)
    assert isinstance(row["reliability_score"], float)
    assert isinstance(row["total_confirmations"], int)
    assert isinstance(row["unique_contributors"], int)
    assert isinstance(row["is_likely_open"], bool)
    assert isinstance(row["is_reliable"], bool)


@pytest.mark.django_db
def test_spot_create_requires_device_header(api_client: APIClient):
    url = "/api/v1/spots"
    response = api_client.post(
        url,
        {"name": "Monastery", "latitude": 18.5, "longitude": 73.8},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_spot_list_returns_results_wrapper(api_client: APIClient):
    spot = SpotFactory()
    url = "/api/v1/spots"
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    response = api_client.get(url)

    assert response.status_code == 200
    assert "results" in response.data
    assert any(item["id"] == str(spot.id) for item in response.data["results"])


@pytest.mark.django_db
def test_vote_endpoint_accepts_duplicate_vote_updates(api_client: APIClient):
    spot = SpotFactory()
    voter = Device.objects.create(device_id="voter-1", username="voter_1")
    url = f"/api/v1/spots/{spot.id}/vote/open"
    api_client.credentials(HTTP_X_DEVICE_ID=voter.device_id)

    first = api_client.post(url, {}, format="json")
    second = api_client.post(url, {}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert spot.votes.filter(device=voter, vote_type="OPEN").count() == 1


@pytest.mark.django_db
def test_vote_requires_manual_captcha_on_suspicious_request(api_client: APIClient, captcha_enforced):
    del captcha_enforced
    spot = SpotFactory()
    voter = Device.objects.create(device_id="voter-2", username="voter_2")
    api_client.credentials(HTTP_X_DEVICE_ID=voter.device_id)
    url = f"/api/v1/spots/{spot.id}/vote/open"

    blocked = api_client.post(url, {"recaptcha_token": "manual-needed"}, format="json")
    assert blocked.status_code == 403
    assert blocked.data["captcha_required"] == "manual"

    allowed = api_client.post(
        url,
        {
            "recaptcha_token": "manual-needed",
            "recaptcha_manual_token": "manual-pass",
        },
        format="json",
    )
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_spot_list_hides_low_karma_notes_from_other_devices(api_client: APIClient):
    trusted_owner = Device.objects.create(device_id="spot-owner-trust", username="owner_trust", karma=80)
    low_author = Device.objects.create(device_id="note-author-low", username="author_low", karma=5)
    other = Device.objects.create(device_id="other-viewer", username="viewer_other", karma=0)
    spot = Spot.objects.create(
        name="Corner",
        latitude=18.5,
        longitude=73.8,
        created_by=trusted_owner,
    )
    SpotNote.objects.create(spot=spot, device=low_author, text="Low karma tip")

    api_client.credentials(HTTP_X_DEVICE_ID=trusted_owner.device_id)
    row_owner = next(r for r in api_client.get("/api/v1/spots").data["results"] if r["id"] == str(spot.id))
    assert "Low karma tip" not in [n["text"] for n in row_owner["notes"]]

    api_client.credentials(HTTP_X_DEVICE_ID=other.device_id)
    row_other = next(r for r in api_client.get("/api/v1/spots").data["results"] if r["id"] == str(spot.id))
    assert "Low karma tip" not in [n["text"] for n in row_other["notes"]]

    api_client.credentials(HTTP_X_DEVICE_ID=low_author.device_id)
    row_author = next(r for r in api_client.get("/api/v1/spots").data["results"] if r["id"] == str(spot.id))
    assert "Low karma tip" in [n["text"] for n in row_author["notes"]]


@pytest.mark.django_db
def test_spot_list_shows_note_from_high_karma_author_to_any_viewer(api_client: APIClient):
    viewer = Device.objects.create(device_id="any-viewer", username="viewer_any", karma=3)
    high_author = Device.objects.create(device_id="trusted-tipper", username="trusted_tipper", karma=80)
    spot = SpotFactory()
    SpotNote.objects.create(spot=spot, device=high_author, text="Trusted tip")

    api_client.credentials(HTTP_X_DEVICE_ID=viewer.device_id)
    row = next(r for r in api_client.get("/api/v1/spots").data["results"] if r["id"] == str(spot.id))
    assert "Trusted tip" in [n["text"] for n in row["notes"]]


@pytest.mark.django_db
def test_spot_list_hides_low_karma_tag_from_other_devices(api_client: APIClient):
    owner = Device.objects.create(device_id="tag-spot-owner", username="tag_owner", karma=80)
    low_tagger = Device.objects.create(device_id="tagger-low", username="tagger_low", karma=5)
    spot = Spot.objects.create(
        name="Stop",
        latitude=18.5,
        longitude=73.8,
        created_by=owner,
    )
    SpotTag.objects.create(spot=spot, tag="cash_only", added_by=low_tagger)

    api_client.credentials(HTTP_X_DEVICE_ID=owner.device_id)
    row_owner = next(r for r in api_client.get("/api/v1/spots").data["results"] if r["id"] == str(spot.id))
    assert "cash_only" not in row_owner["tags"]

    api_client.credentials(HTTP_X_DEVICE_ID=low_tagger.device_id)
    row_tagger = next(r for r in api_client.get("/api/v1/spots").data["results"] if r["id"] == str(spot.id))
    assert "cash_only" in row_tagger["tags"]


@pytest.mark.django_db
def test_identify_throttle_scope_limits_requests(api_client: APIClient, settings):
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["identify"] = "1/min"
    first = api_client.post(
        "/api/v1/device/identify",
        {"device_id": "device-abc", "username": "abc_name"},
        format="json",
    )
    second = api_client.post(
        "/api/v1/device/identify",
        {"device_id": "device-def", "username": "def_name"},
        format="json",
    )
    assert first.status_code == 200
    assert second.status_code == 429


@pytest.mark.django_db
def test_spot_list_accepts_bearer_token_without_x_device_header(api_client: APIClient):
    spot = SpotFactory()
    token = mint_device_access_token(spot.created_by.device_id)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = api_client.get("/api/v1/spots")
    assert response.status_code == 200
    assert any(r["id"] == str(spot.id) for r in response.data["results"])


@pytest.mark.django_db
def test_spot_list_rejects_invalid_lat_lng_query(api_client: APIClient):
    spot = SpotFactory()
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    response = api_client.get("/api/v1/spots?lat=not-a-float&lng=1&radiusM=1000")
    assert response.status_code == 400


@pytest.mark.django_db
def test_spot_list_rejects_radius_above_cap(api_client: APIClient):
    spot = SpotFactory()
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    response = api_client.get(
        f"/api/v1/spots?lat=19&lng=73&radiusM={SPOT_LIST_MAX_RADIUS_M + 1}",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_spot_create_rejects_latitude_out_of_range(api_client: APIClient):
    spot = SpotFactory()
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    response = api_client.post(
        "/api/v1/spots",
        {"latitude": 99.0, "longitude": 73.0, "tags": []},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_spot_note_rejects_text_over_limit(api_client: APIClient):
    spot = SpotFactory()
    api_client.credentials(HTTP_X_DEVICE_ID=spot.created_by.device_id)
    url = f"/api/v1/spots/{spot.id}/notes"
    response = api_client.post(
        url,
        {"text": "x" * (NOTE_TEXT_MAX_LENGTH + 1)},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_engagement_rejects_unknown_type(api_client: APIClient):
    device = Device.objects.create(device_id="engage-device", username="engage_user")
    api_client.credentials(HTTP_X_DEVICE_ID=device.device_id)
    response = api_client.post(
        "/api/v1/events/engagement",
        {"type": "haxxor", "metadata": {}},
        format="json",
    )
    assert response.status_code == 400
