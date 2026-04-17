from __future__ import annotations

from datetime import timedelta
from math import cos
from math import radians
import logging

from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view
from rest_framework import permissions
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from mapmysutta.core.api.serializers import DeviceRegisterSerializer
from mapmysutta.core.api.serializers import DeviceIdentifySerializer
from mapmysutta.core.api.serializers import DeviceIdentitySerializer
from mapmysutta.core.api.serializers import DeviceSerializer
from mapmysutta.core.api.serializers import EmptySerializer
from mapmysutta.core.api.serializers import EngagementEligibleResponseSerializer
from mapmysutta.core.api.serializers import EngagementEventSerializer
from mapmysutta.core.api.serializers import NoteCreatedResponseSerializer
from mapmysutta.core.api.serializers import SpotCreateSerializer
from mapmysutta.core.api.serializers import SpotListSerializer
from mapmysutta.core.api.serializers import SpotTagAppendSerializer
from mapmysutta.core.api.serializers import SpotMapListResponseSerializer
from mapmysutta.core.api.serializers import SpotNoteCreateSerializer
from mapmysutta.core.api.serializers import SpotTimingAggregateSerializer
from mapmysutta.core.api.serializers import SpotUsualTimingUpsertSerializer
from mapmysutta.core.models import Device
from mapmysutta.core.models import EngagementEvent
from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotNote
from mapmysutta.core.models import SpotTag
from mapmysutta.core.models import SpotUsualTiming
from mapmysutta.core.models import SpotVote
from mapmysutta.core.services.area import compute_area_signal
from mapmysutta.core.services.karma import apply_karma
from mapmysutta.core.services.metrics import recompute_spot_metrics
from mapmysutta.core.services.recaptcha import verify_recaptcha_manual
from mapmysutta.core.services.recaptcha import verify_recaptcha_v3
from mapmysutta.core.services.timing import classify_timing_behavior
from mapmysutta.core.services.timing import compute_is_likely_open_now
from mapmysutta.core.services.timing import get_weighted_usual_time
from mapmysutta.core.services.timing import upsert_usual_timing
from mapmysutta.core.services.timing import usual_times_equivalent
from mapmysutta.core.services.voting import apply_vote


logger = logging.getLogger(__name__)


def _parse_bool(value: str | None) -> bool:
    return value in {"1", "true", "True", "yes", "on"}


class HasDevicePermission(permissions.BasePermission):
    def has_permission(self, request: Request, view) -> bool:  # noqa: ARG002
        return request.device is not None


class CaptchaEnforcedAPIView(APIView):
    captcha_action: str = ""

    def _client_ip(self, request: Request) -> str | None:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def require_captcha(
        self,
        *,
        request: Request,
        recaptcha_token: str = "",
        recaptcha_manual_token: str = "",
    ) -> Response | None:
        result = verify_recaptcha_v3(
            token=recaptcha_token,
            action=self.captcha_action,
            remote_ip=self._client_ip(request),
        )
        if result.passed:
            return None
        if result.requires_manual_challenge:
            manual = verify_recaptcha_manual(
                token=recaptcha_manual_token,
                remote_ip=self._client_ip(request),
            )
            if manual.passed:
                return None
            logger.warning(
                "Captcha manual challenge required/failed action=%s reason=%s",
                self.captcha_action,
                result.reason,
            )
            return Response(
                data={
                    "detail": "Additional human verification required.",
                    "captcha_required": "manual",
                    "captcha_reason": result.reason,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        logger.warning(
            "Captcha hard reject action=%s reason=%s",
            self.captcha_action,
            result.reason,
        )
        return Response(
            data={"detail": "Captcha verification failed.", "captcha_reason": result.reason},
            status=status.HTTP_403_FORBIDDEN,
        )


class DeviceRegisterView(CaptchaEnforcedAPIView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = "device_register"
    captcha_action = "register_device"

    @extend_schema(
        tags=["Auth"],
        summary="Register or fetch device",
        description=(
            "Send JSON `{\"device_id\": \"...\"}`. Re-using the same id returns the same device. "
            "After this, use **Authorize** and set the `X-Device-Id` apiKey for all other v1 calls."
        ),
        request=DeviceRegisterSerializer,
        responses={200: DeviceSerializer},
        examples=[
            OpenApiExample(
                "Example body (copy into Request body)",
                value={"device_id": "swagger-demo-550e8400-e29b-41d4-a716-446655440000"},
                request_only=True,
            ),
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = DeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(payload.get("recaptcha_token", "")),
            recaptcha_manual_token=str(payload.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        device, _ = Device.objects.get_or_create(device_id=payload["device_id"])
        data = DeviceSerializer(device).data
        return Response(data=data, status=status.HTTP_200_OK)


class DeviceIdentifyView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = "identify"
    max_attempts_10s = 5
    max_attempts_60s = 20
    max_username_attempts_60s = 5
    soft_lock_seconds = 180

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    def _abuse_key(self, prefix: str, device_id: str, ip_addr: str) -> str:
        return f"identify:{prefix}:{device_id}:{ip_addr}"

    def _increment_window(self, key: str, window_seconds: int) -> int:
        count = cache.get(key)
        if count is None:
            cache.set(key, 1, timeout=window_seconds)
            return 1
        try:
            return int(cache.incr(key))
        except ValueError:
            cache.set(key, 1, timeout=window_seconds)
            return 1

    def _locked_response(self) -> Response:
        return Response(
            data={"code": "too_many_attempts", "detail": "Too many attempts. Try again shortly."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def _is_provisional_username(self, *, device: Device) -> bool:
        """Allow replacing migration-generated username once with real username."""
        username = Device.normalize_username(device.username)
        normalized_device_id = device.device_id.strip().lower()
        return username == normalized_device_id[:20]

    @extend_schema(
        tags=["Auth"],
        summary="Identify device with username",
        description=(
            "Sets immutable device username on first identify. "
            "Subsequent requests for the same device return the existing identity."
        ),
        request=DeviceIdentifySerializer,
        responses={200: DeviceIdentitySerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = DeviceIdentifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        device_id = payload["device_id"]
        username = payload["username"]
        ip_addr = self._client_ip(request)

        lock_key = self._abuse_key("lock", device_id, ip_addr)
        if cache.get(lock_key):
            return self._locked_response()

        count_10s = self._increment_window(self._abuse_key("rate10s", device_id, ip_addr), 10)
        count_60s = self._increment_window(self._abuse_key("rate60s", device_id, ip_addr), 60)
        username_count_60s = self._increment_window(
            self._abuse_key(f"username:{username}", device_id, ip_addr),
            60,
        )
        if (
            count_10s > self.max_attempts_10s
            or count_60s > self.max_attempts_60s
            or username_count_60s > self.max_username_attempts_60s
        ):
            cache.set(lock_key, 1, timeout=self.soft_lock_seconds)
            return self._locked_response()

        existing_device = Device.objects.filter(device_id=device_id).first()
        if (
            existing_device is not None
            and existing_device.username
            and not self._is_provisional_username(device=existing_device)
        ):
            return Response(DeviceIdentitySerializer(existing_device).data, status=status.HTTP_200_OK)

        username_owner = Device.objects.filter(username__iexact=username).first()
        if username_owner is not None and username_owner.device_id != device_id:
            return Response(
                data={"code": "username_taken", "detail": "Username is already taken."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if existing_device is None:
            try:
                existing_device = Device.objects.create(device_id=device_id, username=username)
            except IntegrityError:
                return Response(
                    data={"code": "username_taken", "detail": "Username is already taken."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            existing_device.username = username
            existing_device.save(update_fields=["username"])

        return Response(DeviceIdentitySerializer(existing_device).data, status=status.HTTP_200_OK)


class DeviceDetailView(APIView):
    permission_classes = (HasDevicePermission,)

    @extend_schema(
        tags=["Auth"],
        summary="Current device (karma sync)",
        description="Requires `X-Device-Id`. Returns the same shape as register.",
        responses={200: DeviceSerializer},
    )
    def get(self, request: Request) -> Response:
        assert request.device is not None
        return Response(DeviceSerializer(request.device).data, status=status.HTTP_200_OK)


def _spot_list_prefetch():
    return (
        Prefetch(
            "notes",
            queryset=SpotNote.objects.select_related("device").order_by("-created_at"),
        ),
        Prefetch("tags", queryset=SpotTag.objects.select_related("added_by")),
        "usual_timings",
    )


@extend_schema_view(
    get=extend_schema(
        tags=["Spots"],
        summary="List spots",
        description="Optional bounding box: pass `lat`, `lng`, and `radiusM` together. Requires `X-Device-Id`.",
        parameters=[
            OpenApiParameter("lat", OpenApiTypes.NUMBER, OpenApiParameter.QUERY),
            OpenApiParameter("lng", OpenApiTypes.NUMBER, OpenApiParameter.QUERY),
            OpenApiParameter(
                "radiusM",
                OpenApiTypes.NUMBER,
                OpenApiParameter.QUERY,
                description="Radius in meters (use with lat + lng).",
            ),
            OpenApiParameter("openNow", OpenApiTypes.BOOL, OpenApiParameter.QUERY),
            OpenApiParameter(
                "lateNight",
                OpenApiTypes.BOOL,
                OpenApiParameter.QUERY,
                description="Filter spots tagged `open_late`.",
            ),
            OpenApiParameter("highActivity", OpenApiTypes.BOOL, OpenApiParameter.QUERY),
            OpenApiParameter(
                "includeMine",
                OpenApiTypes.BOOL,
                OpenApiParameter.QUERY,
                description="When true (default), always include spots you created, even outside the radius (e.g. search-placed pins vs GPS).",
            ),
        ],
        responses={200: SpotMapListResponseSerializer},
    ),
    post=extend_schema(
        tags=["Spots"],
        summary="Create spot",
        request=SpotCreateSerializer,
        responses={
            201: SpotListSerializer,
            429: OpenApiResponse(description="Spot creation cooldown (max 1 per minute per device)."),
        },
        examples=[
            OpenApiExample(
                "New spot with tags",
                value={
                    "name": "Late-night counter",
                    "latitude": 18.5204,
                    "longitude": 73.8567,
                    "tags": ["open_late", "cash_only"],
                },
                request_only=True,
            ),
        ],
    ),
)
class SpotListCreateView(CaptchaEnforcedAPIView):
    permission_classes = (HasDevicePermission,)
    spot_create_cooldown_seconds = 120
    throttle_scope = "spot_create"
    captcha_action = "spot_create"

    def get_queryset(self, request: Request) -> QuerySet[Spot]:
        queryset = Spot.objects.filter(is_active=True).select_related("created_by", "metrics")

        lat = request.query_params.get("lat")
        lng = request.query_params.get("lng")
        radius_m = request.query_params.get("radiusM")

        if lat is not None and lng is not None and radius_m is not None:
            lat_value = float(lat)
            lng_value = float(lng)
            radius_value = float(radius_m)
            lat_delta = radius_value / 111_320.0
            lng_denominator = max(cos(radians(lat_value)) * 111_320.0, 1e-6)
            lng_delta = radius_value / lng_denominator
            bbox_q = Q(
                latitude__gte=lat_value - lat_delta,
                latitude__lte=lat_value + lat_delta,
                longitude__gte=lng_value - lng_delta,
                longitude__lte=lng_value + lng_delta,
            )
            assert request.device is not None
            if _parse_bool(request.query_params.get("includeMine", "true")):
                queryset = queryset.filter(bbox_q | Q(created_by=request.device))
            else:
                queryset = queryset.filter(bbox_q)

        if _parse_bool(request.query_params.get("openNow")):
            queryset = queryset.filter(metrics__weighted_score__gt=0)

        if _parse_bool(request.query_params.get("lateNight")):
            queryset = queryset.filter(tags__tag="open_late")

        if _parse_bool(request.query_params.get("highActivity")):
            queryset = queryset.filter(metrics__activity_score__gte=3.0)

        return queryset.distinct().prefetch_related(*_spot_list_prefetch())

    def get(self, request: Request) -> Response:
        queryset = self.get_queryset(request)
        serializer = SpotListSerializer(queryset, many=True, context={"device": request.device})
        area_signal = compute_area_signal(queryset)
        return Response({"area_signal": area_signal, "results": serializer.data}, status=status.HTTP_200_OK)

    def post(self, request: Request) -> Response:
        assert request.device is not None
        cooldown_boundary = timezone.now() - timedelta(seconds=self.spot_create_cooldown_seconds)
        has_recent_spot = Spot.objects.filter(
            created_by=request.device,
            created_at__gte=cooldown_boundary,
        ).exists()
        if has_recent_spot:
            return Response(
                data={"detail": "Spot creation cooldown active. Try again shortly."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = SpotCreateSerializer(data=request.data, context={"device": request.device})
        serializer.is_valid(raise_exception=True)
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(serializer.validated_data.get("recaptcha_token", "")),
            recaptcha_manual_token=str(serializer.validated_data.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        spot = serializer.save()
        recompute_spot_metrics(spot)
        apply_karma(request.device, "ADD_SPOT")

        spot_full = (
            Spot.objects.select_related("metrics", "created_by")
            .prefetch_related(*_spot_list_prefetch())
            .get(id=spot.id)
        )
        response_payload = SpotListSerializer(spot_full, context={"device": request.device}).data
        return Response(response_payload, status=status.HTTP_201_CREATED)


class SpotVoteView(CaptchaEnforcedAPIView):
    permission_classes = (HasDevicePermission,)
    vote_type: str = ""
    throttle_scope = "vote"

    def post(self, request: Request, spot_id) -> Response:
        assert request.device is not None
        serializer = EmptySerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(serializer.validated_data.get("recaptcha_token", "")),
            recaptcha_manual_token=str(serializer.validated_data.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        spot = Spot.objects.filter(id=spot_id, is_active=True).select_related("metrics").first()
        if spot is None:
            return Response({"detail": "Spot not found."}, status=status.HTTP_404_NOT_FOUND)

        apply_vote(spot=spot, device=request.device, vote_type=self.vote_type)
        refreshed_spot = (
            Spot.objects.select_related("metrics", "created_by")
            .prefetch_related(*_spot_list_prefetch())
            .get(id=spot.id)
        )
        payload = SpotListSerializer(refreshed_spot, context={"device": request.device}).data
        return Response(payload, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        tags=["Spots"],
        summary="Vote: spot is open",
        description="No JSON body required. Same device re-posting updates weight only.",
        request=EmptySerializer,
        responses={
            200: SpotListSerializer,
            404: OpenApiResponse(description="Spot not found."),
        },
    ),
)
class SpotVoteOpenView(SpotVoteView):
    vote_type = SpotVote.VoteType.OPEN
    captcha_action = "vote_open"


@extend_schema_view(
    post=extend_schema(
        tags=["Spots"],
        summary="Vote: not sure",
        description="No JSON body required.",
        request=EmptySerializer,
        responses={
            200: SpotListSerializer,
            404: OpenApiResponse(description="Spot not found."),
        },
    ),
)
class SpotVoteNotSureView(SpotVoteView):
    vote_type = SpotVote.VoteType.NOT_SURE
    captcha_action = "vote_not_sure"


class SpotNoteCreateView(CaptchaEnforcedAPIView):
    permission_classes = (HasDevicePermission,)
    max_notes_per_minute = 5
    throttle_scope = "note_create"
    captcha_action = "spot_note"

    @extend_schema(
        tags=["Spots"],
        summary="Add note to spot",
        request=SpotNoteCreateSerializer,
        responses={
            201: NoteCreatedResponseSerializer,
            404: OpenApiResponse(description="Spot not found."),
            429: OpenApiResponse(description="Too many notes per minute."),
        },
        examples=[
            OpenApiExample("Note text", value={"text": "Friendly staff, open till 1am."}, request_only=True),
        ],
    )
    def post(self, request: Request, spot_id) -> Response:
        assert request.device is not None
        spot = Spot.objects.filter(id=spot_id, is_active=True).first()
        if spot is None:
            return Response({"detail": "Spot not found."}, status=status.HTTP_404_NOT_FOUND)

        minute_ago = timezone.now() - timedelta(minutes=1)
        note_count = SpotNote.objects.filter(
            device=request.device,
            created_at__gte=minute_ago,
        ).count()
        if note_count >= self.max_notes_per_minute:
            return Response(
                {"detail": "Note rate limit exceeded."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = SpotNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(serializer.validated_data.get("recaptcha_token", "")),
            recaptcha_manual_token=str(serializer.validated_data.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        note_data = dict(serializer.validated_data)
        note_data.pop("recaptcha_token", None)
        note_data.pop("recaptcha_manual_token", None)
        SpotNote.objects.create(spot=spot, device=request.device, **note_data)
        recompute_spot_metrics(spot)
        apply_karma(request.device, "ADD_NOTE")
        return Response({"status": "created"}, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(
        tags=["Spots"],
        summary="Add a crowd tag to a spot",
        description=(
            "Appends one allowed tag if missing. Duplicate tags are idempotent (no extra karma). "
            "Allowed: `cash_only`, `show_paan`, `cigarettes_only`, `sometimes_closed`, `open_late`."
        ),
        request=SpotTagAppendSerializer,
        responses={
            200: SpotListSerializer,
            404: OpenApiResponse(description="Spot not found."),
        },
        examples=[
            OpenApiExample("Cash only", value={"tag": "cash_only"}, request_only=True),
        ],
    ),
)
class SpotTagAppendView(CaptchaEnforcedAPIView):
    permission_classes = (HasDevicePermission,)
    throttle_scope = "spot_tag"
    captcha_action = "spot_tag"

    def post(self, request: Request, spot_id) -> Response:
        assert request.device is not None
        spot = Spot.objects.filter(id=spot_id, is_active=True).first()
        if spot is None:
            return Response({"detail": "Spot not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SpotTagAppendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(serializer.validated_data.get("recaptcha_token", "")),
            recaptcha_manual_token=str(serializer.validated_data.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        tag_value = serializer.validated_data["tag"]
        _, created = SpotTag.objects.get_or_create(
            spot=spot,
            tag=tag_value,
            defaults={"added_by": request.device},
        )
        if created:
            apply_karma(request.device, "ADD_TAG")
        recompute_spot_metrics(spot)
        refreshed = (
            Spot.objects.select_related("metrics", "created_by")
            .prefetch_related(*_spot_list_prefetch())
            .get(id=spot.id)
        )
        payload = SpotListSerializer(refreshed, context={"device": request.device}).data
        return Response(payload, status=status.HTTP_200_OK)


@extend_schema_view(
    post=extend_schema(
        tags=["Spots"],
        summary="Submit or update usual timing",
        request=SpotUsualTimingUpsertSerializer,
        responses={
            200: SpotTimingAggregateSerializer,
            404: OpenApiResponse(description="Spot not found."),
        },
        examples=[
            OpenApiExample("Usual till 4 AM", value={"usual_open_until": "04:00"}, request_only=True),
        ],
    ),
)
class SpotTimingUpsertView(CaptchaEnforcedAPIView):
    permission_classes = (HasDevicePermission,)
    timing_update_cooldown_seconds = 3600
    throttle_scope = "timing_update"
    captcha_action = "spot_timing"

    def post(self, request: Request, spot_id) -> Response:
        assert request.device is not None
        spot = Spot.objects.filter(id=spot_id, is_active=True).select_related("metrics").first()
        if spot is None:
            return Response({"detail": "Spot not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SpotUsualTimingUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(serializer.validated_data.get("recaptcha_token", "")),
            recaptcha_manual_token=str(serializer.validated_data.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        new_time = serializer.validated_data["usual_open_until"]

        existing = SpotUsualTiming.objects.filter(spot=spot, device=request.device).first()
        cooldown_boundary = timezone.now() - timedelta(seconds=self.timing_update_cooldown_seconds)
        in_cooldown = existing is not None and existing.updated_at >= cooldown_boundary

        if existing is not None and in_cooldown and not usual_times_equivalent(existing.usual_open_until, new_time):
            enriched_spot = Spot.objects.select_related("metrics").prefetch_related("usual_timings").get(id=spot.id)
            avg_time, confidence, variance = get_weighted_usual_time(enriched_spot)
            return Response(
                {
                    "usual_open_until": avg_time.strftime("%H:%M") if avg_time is not None else None,
                    "timing_confidence": confidence,
                    "timing_label": classify_timing_behavior(avg_time, variance),
                    "is_likely_open_now": compute_is_likely_open_now(enriched_spot, avg_time),
                    "timing_update_accepted": False,
                    "detail": "You already updated usual hours recently. Try again in a bit.",
                },
                status=status.HTTP_200_OK,
            )

        if existing is None or not usual_times_equivalent(existing.usual_open_until, new_time):
            upsert_usual_timing(
                spot=spot,
                device=request.device,
                usual_open_until=new_time,
            )
            apply_karma(request.device, "SUBMIT_TIMING")

        enriched_spot = Spot.objects.select_related("metrics").prefetch_related("usual_timings").get(id=spot.id)
        avg_time, confidence, variance = get_weighted_usual_time(enriched_spot)
        payload = {
            "usual_open_until": avg_time.strftime("%H:%M") if avg_time is not None else None,
            "timing_confidence": confidence,
            "timing_label": classify_timing_behavior(avg_time, variance),
            "is_likely_open_now": compute_is_likely_open_now(enriched_spot, avg_time),
            "timing_update_accepted": True,
        }
        return Response(payload, status=status.HTTP_200_OK)


class EngagementEventView(CaptchaEnforcedAPIView):
    permission_classes = (HasDevicePermission,)
    max_events_per_hour = 240
    throttle_scope = "engagement_event"
    captcha_action = "engagement_event"

    @extend_schema(
        tags=["Events"],
        summary="Record engagement (push placeholder)",
        request=EngagementEventSerializer,
        responses={
            200: EngagementEligibleResponseSerializer,
        },
        examples=[
            OpenApiExample(
                "App opened",
                value={"type": "app_open", "metadata": {"screen": "map"}},
                request_only=True,
            ),
        ],
    )
    def post(self, request: Request) -> Response:
        assert request.device is not None
        hour_ago = timezone.now() - timedelta(hours=1)
        event_count = EngagementEvent.objects.filter(
            device=request.device,
            created_at__gte=hour_ago,
        ).count()
        if event_count >= self.max_events_per_hour:
            return Response(
                {"eligible_for_notification": False},
                status=status.HTTP_200_OK,
            )

        serializer = EngagementEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        captcha_resp = self.require_captcha(
            request=request,
            recaptcha_token=str(serializer.validated_data.get("recaptcha_token", "")),
            recaptcha_manual_token=str(serializer.validated_data.get("recaptcha_manual_token", "")),
        )
        if captcha_resp is not None:
            return captcha_resp
        event_data = dict(serializer.validated_data)
        event_data.pop("recaptcha_token", None)
        event_data.pop("recaptcha_manual_token", None)
        event = EngagementEvent.objects.create(device=request.device, **event_data)

        eligible_for_notification = (
            request.device.karma >= 10
            and not EngagementEvent.objects.filter(
                device=request.device,
                type=event.type,
                created_at__gte=timezone.now() - timedelta(hours=24),
            )
            .exclude(id=event.id)
            .exists()
        )
        return Response(
            {"eligible_for_notification": eligible_for_notification},
            status=status.HTTP_200_OK,
        )
