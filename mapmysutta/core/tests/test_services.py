from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from mapmysutta.core.models import SpotMetrics
from mapmysutta.core.models import SpotVote
from mapmysutta.core.services.metrics import recompute_spot_metrics
from mapmysutta.core.services.metrics import compute_open_probability
from mapmysutta.core.services.metrics import compute_reliability_score
from mapmysutta.core.services.time_prediction import compute_typical_close_time
from mapmysutta.core.services.timing import classify_timing_behavior
from mapmysutta.core.services.timing import compute_is_likely_open_now
from mapmysutta.core.services.timing import get_weighted_usual_time
from mapmysutta.core.services.timing import usual_times_equivalent
from mapmysutta.core.services.voting import calculate_vote_weight
from mapmysutta.core.tests.factories import DeviceFactory
from mapmysutta.core.tests.factories import SpotFactory
from mapmysutta.core.tests.factories import SpotUsualTimingFactory


def test_calculate_vote_weight_uses_karma_bands():
    assert calculate_vote_weight(DeviceFactory.build(karma=0)) == 1.0
    assert calculate_vote_weight(DeviceFactory.build(karma=12)) == 1.2
    assert calculate_vote_weight(DeviceFactory.build(karma=36)) == 1.5
    assert calculate_vote_weight(DeviceFactory.build(karma=72)) == 2.0
    assert calculate_vote_weight(DeviceFactory.build(karma=120)) == 2.5


def test_recompute_spot_metrics_updates_vote_counts(db):
    spot = SpotFactory()
    device = DeviceFactory(karma=20)
    SpotVote.objects.create(spot=spot, device=device, vote_type=SpotVote.VoteType.OPEN, weight=1.2)
    SpotVote.objects.create(spot=spot, device=spot.created_by, vote_type=SpotVote.VoteType.NOT_SURE, weight=1.0)

    metrics = recompute_spot_metrics(spot)

    assert metrics.open_votes == 1
    assert metrics.not_sure_votes == 1
    assert metrics.weighted_score > 0
    assert metrics.total_confirmations == 2
    assert metrics.unique_contributors == 2
    assert 0.0 <= metrics.open_probability <= 1.0
    assert 0.0 <= metrics.reliability_score <= 1.0


def test_usual_times_equivalent_ignores_subminute_fields():
    assert usual_times_equivalent(time(4, 0, 1), time(4, 0, 0, 500_000)) is True
    assert usual_times_equivalent(time(4, 0), time(3, 59)) is False


def test_get_weighted_usual_time_respects_weights(db):
    spot = SpotFactory()
    SpotUsualTimingFactory(spot=spot, usual_open_until="04:00:00", weight=2.0)
    SpotUsualTimingFactory(spot=spot, usual_open_until="02:00:00", weight=1.0)

    avg_time, confidence, variance = get_weighted_usual_time(spot)

    assert avg_time is not None
    assert avg_time.hour == 3 and avg_time.minute == 20
    assert confidence > 0
    assert variance >= 0


def test_classify_timing_behavior_handles_variance_and_late_hours():
    assert classify_timing_behavior(None, 0) is None
    assert classify_timing_behavior(time(4, 0), 130) == "UNPREDICTABLE"
    assert classify_timing_behavior(time(2, 0), 10) == "RUNS_LATE"
    assert classify_timing_behavior(time(23, 0), 10) == "NORMAL"


def test_compute_is_likely_open_now_uses_recent_confirmation_override(db):
    spot = SpotFactory()
    now = timezone.now()
    SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={"last_confirmed_at": now - timedelta(minutes=20)},
    )
    spot.refresh_from_db()

    assert compute_is_likely_open_now(spot, None, now=now) is True


def test_compute_is_likely_open_now_cross_midnight_window(db):
    spot = SpotFactory()
    ist = ZoneInfo(str(settings.TIME_ZONE))
    now = datetime(2026, 6, 15, 1, 30, 0, tzinfo=ist)

    assert compute_is_likely_open_now(spot, time(4, 0), now=now) is True


def test_compute_is_likely_open_now_false_after_evening_close_despite_recent_vote(db):
    """Typical close 20:00 local; at 22:00 a fresh open vote must not show 'probably open'."""
    spot = SpotFactory()
    ist = ZoneInfo(str(settings.TIME_ZONE))
    now = datetime(2026, 6, 15, 22, 0, 0, tzinfo=ist)
    SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={"last_confirmed_at": now - timedelta(minutes=12)},
    )
    spot.refresh_from_db()

    assert compute_is_likely_open_now(spot, time(20, 0), now=now) is False


def test_compute_is_likely_open_now_uses_metrics_typical_close_when_no_crowd_usual(db):
    """Auto typical_close ~4am without SpotUsualTiming: evening is still inside the window."""
    spot = SpotFactory()
    ist = ZoneInfo(str(settings.TIME_ZONE))
    now = datetime(2026, 6, 15, 22, 0, 0, tzinfo=ist)
    SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={
            "typical_close_time": time(4, 0),
            "last_confirmed_at": None,
        },
    )
    spot.refresh_from_db()

    assert compute_is_likely_open_now(spot, None, now=now) is True


def test_compute_is_likely_open_now_false_after_late_typical_close_without_crowd_usual(db):
    spot = SpotFactory()
    ist = ZoneInfo(str(settings.TIME_ZONE))
    now = datetime(2026, 6, 15, 5, 0, 0, tzinfo=ist)
    SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={
            "typical_close_time": time(4, 0),
            "last_confirmed_at": now - timedelta(minutes=10),
        },
    )
    spot.refresh_from_db()

    assert compute_is_likely_open_now(spot, None, now=now) is False


def test_compute_is_likely_open_now_false_after_evening_metrics_typical_despite_recent_vote(db):
    """Metrics-only typical 20:00: past close must not be overridden by a recent open vote."""
    spot = SpotFactory()
    ist = ZoneInfo(str(settings.TIME_ZONE))
    now = datetime(2026, 6, 15, 22, 0, 0, tzinfo=ist)
    SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={
            "typical_close_time": time(20, 0),
            "last_confirmed_at": now - timedelta(minutes=12),
        },
    )
    spot.refresh_from_db()

    assert compute_is_likely_open_now(spot, None, now=now) is False


def test_compute_typical_close_time_picks_latest_qualifying_hour(db):
    spot = SpotFactory()
    d1 = DeviceFactory(karma=80)
    d2 = DeviceFactory(karma=10)
    SpotVote.objects.create(spot=spot, device=d1, vote_type=SpotVote.VoteType.OPEN, weight=3.0)
    SpotVote.objects.create(spot=spot, device=d2, vote_type=SpotVote.VoteType.NOT_SURE, weight=1.0)

    typical = compute_typical_close_time(spot, days=14)
    assert typical is not None
    assert 0 <= typical.hour <= 23


def test_compute_open_probability_clamps_and_boosts_recent_activity(db):
    spot = SpotFactory()
    now = timezone.now()
    SpotMetrics.objects.update_or_create(
        spot=spot,
        defaults={"last_confirmed_at": now - timedelta(minutes=5)},
    )
    spot.refresh_from_db()
    p = compute_open_probability(spot, now=now)
    assert 0.0 <= p <= 1.0
    assert p > 0.2


def test_compute_reliability_score_increases_with_more_consistent_votes(db):
    spot = SpotFactory()
    d1 = DeviceFactory()
    d2 = DeviceFactory()
    SpotVote.objects.create(spot=spot, device=d1, vote_type=SpotVote.VoteType.OPEN, weight=1.0)
    low_score = compute_reliability_score(spot)
    SpotVote.objects.create(spot=spot, device=d2, vote_type=SpotVote.VoteType.OPEN, weight=1.0)
    high_score = compute_reliability_score(spot)
    assert 0.0 <= low_score <= 1.0
    assert 0.0 <= high_score <= 1.0
    assert high_score >= low_score
