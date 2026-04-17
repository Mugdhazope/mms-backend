from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
from math import sqrt

from django.utils import timezone

from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotUsualTiming

MIN_USUAL_TIME = time(hour=18, minute=0)
MAX_USUAL_TIME = time(hour=6, minute=0)
RECENT_CONFIRMATION_WINDOW_MINUTES = 90
UNPREDICTABLE_VARIANCE_THRESHOLD_MINUTES = 120.0


def normalize_timing_weight(device_karma: int) -> float:
    if device_karma < 1:
        return 0.0
    if device_karma < 10:
        return 1.0
    if device_karma < 30:
        return 1.3
    if device_karma < 60:
        return 1.8
    return 2.3


def time_to_minutes(value: time) -> int:
    return (value.hour * 60) + value.minute


def usual_times_equivalent(a: time, b: time) -> bool:
    """Compare clock times at minute precision (DB / serializers may differ in seconds)."""
    return a.hour == b.hour and a.minute == b.minute


def _time_to_night_minutes(value: time) -> int:
    minutes = time_to_minutes(value)
    return minutes + (24 * 60 if minutes < (6 * 60) else 0)


def minutes_to_time(value: int) -> time:
    normalized = value % (24 * 60)
    return time(hour=normalized // 60, minute=normalized % 60)


def is_reasonable_usual_time(value: time) -> bool:
    return value >= MIN_USUAL_TIME or value <= MAX_USUAL_TIME


def get_weighted_usual_time(spot: Spot) -> tuple[time | None, float, float]:
    timings = list(spot.usual_timings.all())
    if not timings:
        return None, 0.0, 0.0

    weighted_sum = 0.0
    total_weight = 0.0
    weighted_values: list[tuple[float, float]] = []

    for timing in timings:
        weight = max(0.0, float(timing.weight))
        if weight <= 0:
            continue
        minutes = float(_time_to_night_minutes(timing.usual_open_until))
        weighted_sum += minutes * weight
        total_weight += weight
        weighted_values.append((minutes, weight))

    if total_weight <= 0:
        return None, 0.0, 0.0

    mean_minutes = weighted_sum / total_weight
    variance = sum(weight * ((minutes - mean_minutes) ** 2) for minutes, weight in weighted_values) / total_weight
    stddev = sqrt(variance)

    # Confidence rises with participation and total signal weight.
    confidence = min(1.0, (len(weighted_values) / 5.0) * 0.6 + (total_weight / 10.0) * 0.4)
    return minutes_to_time(int(round(mean_minutes))), round(confidence, 4), round(stddev, 2)


def classify_timing_behavior(avg_time: time | None, variance: float) -> str | None:
    if avg_time is None:
        return None
    if variance >= UNPREDICTABLE_VARIANCE_THRESHOLD_MINUTES:
        return "UNPREDICTABLE"
    if time_to_minutes(avg_time) <= (2 * 60):
        return "RUNS_LATE"
    return "NORMAL"


def _is_before_usual_close(now_local: time, usual_open_until: time) -> bool:
    if usual_open_until >= MIN_USUAL_TIME:
        return now_local <= usual_open_until or now_local <= MAX_USUAL_TIME
    return now_local <= usual_open_until or now_local >= MIN_USUAL_TIME


def compute_is_likely_open_now(spot: Spot, avg_time: time | None, now: datetime | None = None) -> bool:
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current, timezone.get_current_timezone())
    # Compare usual-close window in local wall clock (app TIME_ZONE), not UTC.
    now_local = timezone.localtime(current).time()

    if avg_time is not None:
        if _is_before_usual_close(now_local, avg_time):
            return True
        # Past usual close for this model: do not let a fresh "open" vote claim open-now.
        return False

    metrics = getattr(spot, "metrics", None)
    typical_close = getattr(metrics, "typical_close_time", None) if metrics else None
    # No crowd-submitted usual hours: infer the open window from auto typical_close_time
    # (e.g. metrics say "open until ~4am") so 22:00 is still inside the window.
    if typical_close is not None and is_reasonable_usual_time(typical_close):
        if _is_before_usual_close(now_local, typical_close):
            return True
        return False

    if metrics is None or metrics.last_confirmed_at is None:
        return False

    recent_boundary = current - timedelta(minutes=RECENT_CONFIRMATION_WINDOW_MINUTES)
    return metrics.last_confirmed_at >= recent_boundary


def upsert_usual_timing(spot: Spot, device, usual_open_until: time) -> SpotUsualTiming:
    weight = normalize_timing_weight(device.karma)
    timing, _ = SpotUsualTiming.objects.update_or_create(
        spot=spot,
        device=device,
        defaults={"usual_open_until": usual_open_until, "weight": weight},
    )
    return timing
