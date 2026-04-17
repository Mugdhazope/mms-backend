from django.urls import path

from mapmysutta.core.api.views import DeviceDetailView
from mapmysutta.core.api.views import DeviceIdentifyView
from mapmysutta.core.api.views import EngagementEventView
from mapmysutta.core.api.views import SpotListCreateView
from mapmysutta.core.api.views import SpotNoteCreateView
from mapmysutta.core.api.views import SpotTagAppendView
from mapmysutta.core.api.views import SpotTimingUpsertView
from mapmysutta.core.api.views import SpotVoteNotSureView
from mapmysutta.core.api.views import SpotVoteOpenView

app_name = "core-api"

urlpatterns = [
    path("device/identify", DeviceIdentifyView.as_view(), name="device-identify"),
    path("auth/device", DeviceDetailView.as_view(), name="device-detail"),
    path("spots", SpotListCreateView.as_view(), name="spots-list-create"),
    path("spots/<uuid:spot_id>/vote/open", SpotVoteOpenView.as_view(), name="spots-vote-open"),
    path("spots/<uuid:spot_id>/vote/not-sure", SpotVoteNotSureView.as_view(), name="spots-vote-not-sure"),
    path("spots/<uuid:spot_id>/notes", SpotNoteCreateView.as_view(), name="spots-notes"),
    path("spots/<uuid:spot_id>/tags", SpotTagAppendView.as_view(), name="spots-tags-append"),
    path("spots/<uuid:spot_id>/timing", SpotTimingUpsertView.as_view(), name="spots-timing-upsert"),
    path("events/engagement", EngagementEventView.as_view(), name="engagement-events"),
]
