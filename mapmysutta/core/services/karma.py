from __future__ import annotations

from dataclasses import dataclass

from mapmysutta.core.models import Device


@dataclass(frozen=True)
class KarmaAction:
    key: str
    points: int


KARMA_ACTIONS = {
    "ADD_SPOT": KarmaAction(key="ADD_SPOT", points=5),
    "CONFIRM_OPEN": KarmaAction(key="CONFIRM_OPEN", points=2),
    "NOT_SURE": KarmaAction(key="NOT_SURE", points=1),
    "ADD_NOTE": KarmaAction(key="ADD_NOTE", points=3),
    "ADD_TAG": KarmaAction(key="ADD_TAG", points=2),
    "SUBMIT_TIMING": KarmaAction(key="SUBMIT_TIMING", points=2),
    "CREATOR_VALIDATED": KarmaAction(key="CREATOR_VALIDATED", points=5),
}


def apply_karma(device: Device, action_type: str) -> Device:
    action = KARMA_ACTIONS.get(action_type)
    if action is None:
        return device

    device.karma += action.points
    device.trust_score = min(100.0, float(device.karma) / 2.0)
    device.save(update_fields=["karma", "trust_score"])
    return device
