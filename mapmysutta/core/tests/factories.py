from __future__ import annotations

from factory import SubFactory
from factory import Faker
from factory.django import DjangoModelFactory

from mapmysutta.core.models import Device
from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotUsualTiming


class DeviceFactory(DjangoModelFactory[Device]):
    device_id = Faker("uuid4")
    username = Faker("user_name")
    karma = 0
    trust_score = 0.0

    class Meta:
        model = Device


class SpotFactory(DjangoModelFactory[Spot]):
    name = Faker("street_name")
    latitude = 18.5204
    longitude = 73.8567
    created_by = SubFactory(DeviceFactory)
    is_active = True

    class Meta:
        model = Spot


class SpotUsualTimingFactory(DjangoModelFactory[SpotUsualTiming]):
    spot = SubFactory(SpotFactory)
    device = SubFactory(DeviceFactory)
    usual_open_until = "04:00:00"
    weight = 1.0

    class Meta:
        model = SpotUsualTiming
