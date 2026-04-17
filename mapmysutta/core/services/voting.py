from __future__ import annotations

from django.db import transaction

from mapmysutta.core.models import Device
from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotVote
from mapmysutta.core.services.karma import apply_karma
from mapmysutta.core.services.metrics import recompute_spot_metrics


def calculate_vote_weight(device: Device) -> float:
    if device.karma < 10:
        return 0.35
    if device.karma < 25:
        return 0.65
    if device.karma < 50:
        return 1.2
    if device.karma < 100:
        return 1.5
    return 2.0


@transaction.atomic
def apply_vote(spot: Spot, device: Device, vote_type: str) -> SpotVote:
    weight = calculate_vote_weight(device)
    vote, created = SpotVote.objects.get_or_create(
        spot=spot,
        device=device,
        defaults={"vote_type": vote_type, "weight": weight},
    )
    if not created and (vote.weight != weight or vote.vote_type != vote_type):
        vote.weight = weight
        vote.vote_type = vote_type
        vote.save(update_fields=["weight", "vote_type"])

    if vote_type == SpotVote.VoteType.OPEN:
        apply_karma(device=device, action_type="CONFIRM_OPEN")
    else:
        apply_karma(device=device, action_type="NOT_SURE")

    recompute_spot_metrics(spot)
    return vote
