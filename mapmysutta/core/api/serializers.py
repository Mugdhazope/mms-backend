from __future__ import annotations

import json
import re

from rest_framework import serializers

from mapmysutta.core.device_token import mint_device_access_token
from mapmysutta.core.limits import ENGAGEMENT_METADATA_MAX_JSON_BYTES
from mapmysutta.core.limits import ENGAGEMENT_METADATA_MAX_KEYS
from mapmysutta.core.limits import NOTE_TEXT_MAX_LENGTH
from mapmysutta.core.limits import SPOT_NAME_MAX_LENGTH
from mapmysutta.core.models import Device
from mapmysutta.core.models import EngagementEvent
from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotMetrics
from mapmysutta.core.models import SpotNote
from mapmysutta.core.models import SpotTag
from mapmysutta.core.security import is_valid_device_id
from mapmysutta.core.services.timing import classify_timing_behavior
from mapmysutta.core.services.timing import compute_is_likely_open_now
from mapmysutta.core.services.timing import get_weighted_usual_time
from mapmysutta.core.services.timing import is_reasonable_usual_time
from mapmysutta.core.trust import TRUSTED_CONTRIBUTOR_MIN_KARMA


class CaptchaPayloadSerializer(serializers.Serializer):
    recaptcha_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    recaptcha_manual_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )


class DeviceRegisterSerializer(serializers.Serializer):
    device_id = serializers.CharField(
        max_length=255,
        help_text="Stable identifier from the client (UUID string recommended).",
    )
    recaptcha_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    recaptcha_manual_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )

    def validate_device_id(self, value):
        if not is_valid_device_id(value):
            raise serializers.ValidationError(
                "Invalid device_id. Use 8-255 chars: letters, numbers, ., _, :, -",
            )
        return value.strip()


USERNAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "administrator",
        "support",
        "system",
        "root",
        "null",
        "undefined",
        "spam",
    },
)


class DeviceIdentifySerializer(serializers.Serializer):
    device_id = serializers.CharField(
        max_length=255,
        help_text="Stable identifier from the client (UUID string recommended).",
    )
    username = serializers.CharField(max_length=20, min_length=3)

    def validate_device_id(self, value):
        if not is_valid_device_id(value):
            raise serializers.ValidationError(
                "Invalid device_id. Use 8-255 chars: letters, numbers, ., _, :, -",
            )
        return value.strip()

    def validate_username(self, value: str) -> str:
        normalized = Device.normalize_username(value)
        if len(normalized) < 3 or len(normalized) > 20:
            raise serializers.ValidationError("Username must be between 3 and 20 characters.")
        if normalized in RESERVED_USERNAMES:
            raise serializers.ValidationError("Username is not allowed.")
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise serializers.ValidationError(
                "Username can only contain lowercase letters, numbers, and underscore.",
            )
        return normalized


class EmptySerializer(CaptchaPayloadSerializer):
    """POST body not used; schema placeholder for Swagger."""


class NoteCreatedResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class EngagementEligibleResponseSerializer(serializers.Serializer):
    eligible_for_notification = serializers.BooleanField()


class DeviceSerializer(serializers.ModelSerializer[Device]):
    access_token = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ("device_id", "username", "karma", "trust_score", "access_token")

    def get_access_token(self, obj: Device) -> str:
        return mint_device_access_token(obj.device_id)


class DeviceIdentitySerializer(serializers.ModelSerializer[Device]):
    access_token = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = ("device_id", "username", "karma", "access_token")

    def get_access_token(self, obj: Device) -> str:
        return mint_device_access_token(obj.device_id)


_ZW_CHARS = "\u200b\u200c\u200d\ufeff"


def _normalize_spot_name(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    for ch in _ZW_CHARS:
        s = s.replace(ch, "")
    if len(s) > SPOT_NAME_MAX_LENGTH:
        msg = f"Name must be at most {SPOT_NAME_MAX_LENGTH} characters."
        raise serializers.ValidationError(msg)
    if any(ord(c) < 32 for c in s):
        msg = "Name contains invalid characters."
        raise serializers.ValidationError(msg)
    return s


ALLOWED_ENGAGEMENT_EVENT_TYPES = frozenset(
    {
        "spot_added_nearby",
        "spot_confirmed_nearby",
        "area_activity_spike",
        "app_open",
    },
)


ALLOWED_SPOT_TAG_VALUES = frozenset(
    {
        "cash_only",
        "show_paan",
        "cigarettes_only",
        "sometimes_closed",
        "open_late",
    },
)


class SpotTagAppendSerializer(CaptchaPayloadSerializer):
    tag = serializers.CharField(max_length=64)

    def validate_tag(self, value: str) -> str:
        normalized = value.strip()
        if normalized not in ALLOWED_SPOT_TAG_VALUES:
            msg = "Unknown or unsupported tag."
            raise serializers.ValidationError(msg)
        return normalized


class SpotCreateSerializer(CaptchaPayloadSerializer, serializers.ModelSerializer[Spot]):
    name = serializers.CharField(
        max_length=SPOT_NAME_MAX_LENGTH,
        required=False,
        allow_null=True,
        allow_blank=True,
        trim_whitespace=True,
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        allow_empty=True,
        write_only=True,
    )

    class Meta:
        model = Spot
        fields = (
            "id",
            "name",
            "latitude",
            "longitude",
            "tags",
            "recaptcha_token",
            "recaptcha_manual_token",
        )
        read_only_fields = ("id",)

    def validate_name(self, value: str | None) -> str | None:
        return _normalize_spot_name(value)

    def validate_latitude(self, value: float) -> float:
        if value < -90.0 or value > 90.0:
            msg = "latitude must be between -90 and 90."
            raise serializers.ValidationError(msg)
        return value

    def validate_longitude(self, value: float) -> float:
        if value < -180.0 or value > 180.0:
            msg = "longitude must be between -180 and 180."
            raise serializers.ValidationError(msg)
        return value

    def create(self, validated_data):
        validated_data.pop("recaptcha_token", None)
        validated_data.pop("recaptcha_manual_token", None)
        tags = validated_data.pop("tags", [])
        spot = Spot.objects.create(created_by=self.context["device"], **validated_data)
        if tags:
            SpotTag.objects.bulk_create([SpotTag(spot=spot, tag=tag) for tag in set(tags)])
        return spot


class SpotListSerializer(serializers.ModelSerializer[Spot]):
    _NOTES_CAP = 10

    open_votes = serializers.SerializerMethodField()
    not_sure_votes = serializers.SerializerMethodField()
    weighted_score = serializers.SerializerMethodField()
    activity_score = serializers.SerializerMethodField()
    last_confirmed_at = serializers.SerializerMethodField()
    is_own_spot = serializers.SerializerMethodField()
    tags = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()
    usual_open_until = serializers.SerializerMethodField()
    timing_confidence = serializers.SerializerMethodField()
    timing_label = serializers.SerializerMethodField()
    is_likely_open_now = serializers.SerializerMethodField()
    typical_close_time = serializers.SerializerMethodField()
    open_probability = serializers.SerializerMethodField()
    reliability_score = serializers.SerializerMethodField()
    total_confirmations = serializers.SerializerMethodField()
    unique_contributors = serializers.SerializerMethodField()
    is_likely_open = serializers.SerializerMethodField()
    is_reliable = serializers.SerializerMethodField()

    class Meta:
        model = Spot
        fields = (
            "id",
            "name",
            "latitude",
            "longitude",
            "open_votes",
            "not_sure_votes",
            "weighted_score",
            "activity_score",
            "last_confirmed_at",
            "is_own_spot",
            "tags",
            "notes",
            "usual_open_until",
            "timing_confidence",
            "timing_label",
            "is_likely_open_now",
            "typical_close_time",
            "open_probability",
            "reliability_score",
            "total_confirmations",
            "unique_contributors",
            "is_likely_open",
            "is_reliable",
        )

    def get_is_own_spot(self, obj: Spot) -> bool:
        device = self.context.get("device")
        return bool(device and obj.created_by_id == device.id)

    @staticmethod
    def _metrics_or_none(obj: Spot) -> SpotMetrics | None:
        try:
            return obj.metrics
        except SpotMetrics.DoesNotExist:
            return None

    def get_open_votes(self, obj: Spot) -> int:
        m = self._metrics_or_none(obj)
        return m.open_votes if m else 0

    def get_not_sure_votes(self, obj: Spot) -> int:
        m = self._metrics_or_none(obj)
        return m.not_sure_votes if m else 0

    def get_weighted_score(self, obj: Spot) -> float:
        m = self._metrics_or_none(obj)
        return float(m.weighted_score) if m else 0.0

    def get_activity_score(self, obj: Spot) -> float:
        m = self._metrics_or_none(obj)
        return float(m.activity_score) if m else 0.0

    def get_last_confirmed_at(self, obj: Spot):
        m = self._metrics_or_none(obj)
        return m.last_confirmed_at if m else None

    def get_tags(self, obj: Spot) -> list[str]:
        device = self.context.get("device")
        viewer_pk = device.id if device else None
        out: list[str] = []
        for t in obj.tags.all():
            if t.added_by_id is None:
                out.append(t.tag)
                continue
            if viewer_pk is not None and t.added_by_id == viewer_pk:
                out.append(t.tag)
                continue
            author = t.added_by
            if author is not None and author.karma >= TRUSTED_CONTRIBUTOR_MIN_KARMA:
                out.append(t.tag)
        return out

    def get_notes(self, obj: Spot) -> list[dict[str, object]]:
        device = self.context.get("device")
        viewer_pk = device.id if device else None
        out: list[dict[str, object]] = []
        for n in obj.notes.all():
            if len(out) >= self._NOTES_CAP:
                break
            author = n.device
            if (viewer_pk is not None and author.id == viewer_pk) or author.karma >= TRUSTED_CONTRIBUTOR_MIN_KARMA:
                out.append({"text": n.text, "created_at": n.created_at})
        return out

    def _timing_payload(self, obj: Spot) -> tuple[object, float, float]:
        payload = self.context.setdefault("_timing_cache", {})
        cache_key = str(obj.id)
        if cache_key not in payload:
            payload[cache_key] = get_weighted_usual_time(obj)
        return payload[cache_key]

    def get_usual_open_until(self, obj: Spot) -> str | None:
        avg_time, _, _ = self._timing_payload(obj)
        return avg_time.strftime("%H:%M") if avg_time is not None else None

    def get_timing_confidence(self, obj: Spot) -> float:
        _, confidence, _ = self._timing_payload(obj)
        return confidence

    def get_timing_label(self, obj: Spot) -> str | None:
        avg_time, _, variance = self._timing_payload(obj)
        return classify_timing_behavior(avg_time, variance)

    def get_is_likely_open_now(self, obj: Spot) -> bool:
        avg_time, _, _ = self._timing_payload(obj)
        return compute_is_likely_open_now(obj, avg_time)

    def get_typical_close_time(self, obj: Spot) -> str | None:
        m = self._metrics_or_none(obj)
        if m is None or m.typical_close_time is None:
            return None
        return m.typical_close_time.strftime("%H:%M")

    def get_open_probability(self, obj: Spot) -> float:
        m = self._metrics_or_none(obj)
        return float(m.open_probability) if m else 0.0

    def get_reliability_score(self, obj: Spot) -> float:
        m = self._metrics_or_none(obj)
        return float(m.reliability_score) if m else 0.0

    def get_total_confirmations(self, obj: Spot) -> int:
        m = self._metrics_or_none(obj)
        return int(m.total_confirmations) if m else 0

    def get_unique_contributors(self, obj: Spot) -> int:
        m = self._metrics_or_none(obj)
        return int(m.unique_contributors) if m else 0

    def get_is_likely_open(self, obj: Spot) -> bool:
        return self.get_open_probability(obj) > 0.65

    def get_is_reliable(self, obj: Spot) -> bool:
        return self.get_reliability_score(obj) > 0.6


class SpotMapListResponseSerializer(serializers.Serializer):
    area_signal = serializers.ChoiceField(choices=["HOT", "ACTIVE", "DEAD"])
    results = SpotListSerializer(many=True)


class SpotNoteCreateSerializer(serializers.ModelSerializer[SpotNote]):
    text = serializers.CharField(
        max_length=NOTE_TEXT_MAX_LENGTH,
        trim_whitespace=True,
    )
    recaptcha_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        write_only=True,
    )
    recaptcha_manual_token = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        write_only=True,
    )

    class Meta:
        model = SpotNote
        fields = ("text", "recaptcha_token", "recaptcha_manual_token")


class EngagementEventSerializer(
    CaptchaPayloadSerializer,
    serializers.ModelSerializer[EngagementEvent],
):
    metadata = serializers.JSONField(required=False, default=dict)

    class Meta:
        model = EngagementEvent
        fields = ("type", "metadata", "recaptcha_token", "recaptcha_manual_token")

    def validate_type(self, value: str) -> str:
        normalized = value.strip()
        if normalized not in ALLOWED_ENGAGEMENT_EVENT_TYPES:
            msg = "Unknown or unsupported engagement type."
            raise serializers.ValidationError(msg)
        return normalized

    def validate_metadata(self, value: dict) -> dict:
        if not isinstance(value, dict):
            msg = "metadata must be a JSON object."
            raise serializers.ValidationError(msg)
        if len(value) > ENGAGEMENT_METADATA_MAX_KEYS:
            msg = "metadata has too many keys."
            raise serializers.ValidationError(msg)
        try:
            raw = json.dumps(value, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            msg = "metadata must be JSON-serializable."
            raise serializers.ValidationError(msg) from exc
        if len(raw.encode("utf-8")) > ENGAGEMENT_METADATA_MAX_JSON_BYTES:
            msg = "metadata is too large."
            raise serializers.ValidationError(msg)
        return value


class SpotUsualTimingUpsertSerializer(CaptchaPayloadSerializer):
    usual_open_until = serializers.TimeField()

    def validate_usual_open_until(self, value):
        if not is_reasonable_usual_time(value):
            raise serializers.ValidationError("Time must be between 18:00 and 06:00.")
        return value


class SpotTimingAggregateSerializer(serializers.Serializer):
    usual_open_until = serializers.CharField(allow_null=True)
    timing_confidence = serializers.FloatField()
    timing_label = serializers.CharField(allow_null=True)
    is_likely_open_now = serializers.BooleanField()
    timing_update_accepted = serializers.BooleanField(required=False)
    detail = serializers.CharField(required=False, allow_blank=True)
