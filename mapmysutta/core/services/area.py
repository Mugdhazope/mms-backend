from __future__ import annotations

from django.db.models import QuerySet

from mapmysutta.core.models import Spot
from mapmysutta.core.services.metrics import aggregate_area_stats


def compute_area_signal(spots_queryset: QuerySet[Spot]) -> str:
    stats = aggregate_area_stats(spots_queryset)
    total = stats["total"] or 0
    if total == 0:
        return "DEAD"

    hot = stats["hot"] or 0
    active = stats["active"] or 0
    recent_open_votes = stats["recent_open_votes"] or 0

    if hot >= max(1, total // 3) or recent_open_votes >= 10:
        return "HOT"
    if active >= max(1, total // 4) or recent_open_votes >= 3:
        return "ACTIVE"
    return "DEAD"
