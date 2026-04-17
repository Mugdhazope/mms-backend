from __future__ import annotations

from datetime import timedelta

from django.db.models import Count
from django.db.models import Max
from django.db.models import Q
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotMetrics
from mapmysutta.core.models import SpotVote
from mapmysutta.core.services.time_prediction import compute_hourly_open_probabilities
from mapmysutta.core.services.time_prediction import compute_typical_close_time

RECENT_ACTIVITY_WINDOW_MINUTES = 30
LOOKBACK_DAYS = 14


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_open_probability(spot: Spot, now=None) -> float:
    """Blend time-pattern probability with recent activity."""
    current = now or timezone.now()
    hourly = compute_hourly_open_probabilities(spot, days=LOOKBACK_DAYS)
    time_based_score = float(hourly.get(current.hour, 0.0))

    metrics = getattr(spot, "metrics", None)
    recent_activity_score = 0.0
    if metrics is not None and metrics.last_confirmed_at is not None:
        age_minutes = (current - metrics.last_confirmed_at).total_seconds() / 60.0
        recent_activity_score = _clamp01(1.0 - (age_minutes / float(RECENT_ACTIVITY_WINDOW_MINUTES)))

    return round(_clamp01((time_based_score * 0.6) + (recent_activity_score * 0.4)), 4)


def compute_reliability_score(spot: Spot) -> float:
    """Probability that a spot is real/reliable from crowd behavior."""
    votes = SpotVote.objects.filter(spot=spot)
    total_votes = votes.count()
    if total_votes == 0:
        return 0.0

    unique_devices = votes.values("device_id").distinct().count()
    open_count = votes.filter(vote_type=SpotVote.VoteType.OPEN).count()
    not_sure_count = votes.filter(vote_type=SpotVote.VoteType.NOT_SURE).count()

    # Saturate at practical confidence plateaus.
    unique_devices_weight = _clamp01(unique_devices / 12.0)
    total_votes_weight = _clamp01(total_votes / 30.0)
    consistency_weight = _clamp01(open_count / max(1, open_count + not_sure_count))

    score = (
        (unique_devices_weight * 0.4)
        + (total_votes_weight * 0.3)
        + (consistency_weight * 0.3)
    )
    return round(_clamp01(score), 4)


def recompute_spot_metrics(spot: Spot) -> SpotMetrics:
    now = timezone.now()
    recent_window = now - timedelta(hours=24)
    votes = SpotVote.objects.filter(spot=spot)

    open_votes = votes.filter(vote_type=SpotVote.VoteType.OPEN)
    not_sure_votes = votes.filter(vote_type=SpotVote.VoteType.NOT_SURE)

    open_count = open_votes.count()
    not_sure_count = not_sure_votes.count()
    open_weight = open_votes.aggregate(value=Coalesce(Sum("weight"), 0.0))["value"]
    not_sure_weight = not_sure_votes.aggregate(value=Coalesce(Sum("weight"), 0.0))["value"]
    weighted_score = float(open_weight) - float(not_sure_weight) * 0.75

    recent_open_votes = open_votes.filter(created_at__gte=recent_window)
    recent_confirmations = recent_open_votes.count()
    last_confirmed_at = open_votes.aggregate(last=Max("created_at"))["last"]
    activity_score = float(recent_confirmations) + max(0.0, weighted_score * 0.1)
    total_confirmations = open_count + not_sure_count
    unique_contributors = votes.values("device_id").distinct().count()
    typical_close_time = compute_typical_close_time(spot, days=LOOKBACK_DAYS)
    reliability_score = compute_reliability_score(spot)

    metrics, _ = SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={
            "open_votes": open_count,
            "not_sure_votes": not_sure_count,
            "weighted_score": weighted_score,
            "activity_score": activity_score,
            "last_confirmed_at": last_confirmed_at,
            "total_confirmations": total_confirmations,
            "unique_contributors": unique_contributors,
            "typical_close_time": typical_close_time,
            "reliability_score": reliability_score,
            "recent_confirmations": recent_confirmations,
        },
    )
    spot.metrics = metrics
    metrics.open_probability = compute_open_probability(spot, now=now)
    metrics.save(update_fields=["open_probability"])
    return metrics


def aggregate_area_stats(spots_queryset):
    since = timezone.now() - timedelta(hours=24)
    return spots_queryset.select_related("metrics").aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(metrics__activity_score__gte=2.0)),
        hot=Count("id", filter=Q(metrics__activity_score__gte=5.0)),
        recent_confirms=Coalesce(Sum("metrics__recent_confirmations"), 0),
        recent_open_votes=Count("votes", filter=Q(votes__vote_type=SpotVote.VoteType.OPEN, votes__created_at__gte=since)),
    )
