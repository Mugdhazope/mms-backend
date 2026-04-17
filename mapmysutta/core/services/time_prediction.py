from __future__ import annotations

from datetime import time
from datetime import timedelta

from django.db.models import FloatField
from django.db.models import Q
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.db.models.functions import ExtractHour
from django.utils import timezone

from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotVote

DEFAULT_VOTE_LOOKBACK_DAYS = 14
OPEN_PROBABILITY_THRESHOLD = 0.6


def compute_hourly_open_probabilities(spot: Spot, days: int = DEFAULT_VOTE_LOOKBACK_DAYS) -> dict[int, float]:
    """Return open-probability per hour (0-23) from weighted recent votes."""
    since = timezone.now() - timedelta(days=max(1, days))
    rows = (
        SpotVote.objects.filter(spot=spot, created_at__gte=since)
        .annotate(hour=ExtractHour("created_at"))
        .values("hour")
        .annotate(
            open_weight=Coalesce(
                Sum(
                    "weight",
                    filter=Q(vote_type=SpotVote.VoteType.OPEN),
                    output_field=FloatField(),
                ),
                0.0,
            ),
            total_weight=Coalesce(Sum("weight", output_field=FloatField()), 0.0),
        )
    )

    out: dict[int, float] = {}
    for row in rows:
        hour = row.get("hour")
        if hour is None:
            continue
        total_weight = float(row["total_weight"])
        if total_weight <= 0:
            continue
        open_weight = float(row["open_weight"])
        out[int(hour)] = max(0.0, min(1.0, open_weight / total_weight))
    return out


def compute_typical_close_time(spot: Spot, days: int = DEFAULT_VOTE_LOOKBACK_DAYS) -> time | None:
    hourly = compute_hourly_open_probabilities(spot, days=days)
    qualifying = [h for h, p in hourly.items() if p > OPEN_PROBABILITY_THRESHOLD]
    if not qualifying:
        return None
    close_hour = max(qualifying)
    return time(hour=close_hour, minute=0)
