from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from mapmysutta.core.models import Device
from mapmysutta.core.models import EngagementEvent
from mapmysutta.core.models import Spot
from mapmysutta.core.models import SpotMetrics
from mapmysutta.core.models import SpotNote
from mapmysutta.core.models import SpotTag
from mapmysutta.core.models import SpotVote


class SpotTagInline(admin.TabularInline):
    model = SpotTag
    extra = 0


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("device_id", "karma", "trust_score", "last_active_at", "created_at")
    list_filter = ("created_at", "last_active_at")
    search_fields = ("device_id", "id")
    readonly_fields = ("id", "created_at")
    ordering = ("-created_at",)


@admin.register(Spot)
class SpotAdmin(admin.ModelAdmin):
    list_display = (
        "short_id",
        "name",
        "latitude",
        "longitude",
        "created_by",
        "is_active",
        "metrics_summary",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "id", "created_by__device_id")
    readonly_fields = ("id", "created_at", "metrics_readonly_block")
    raw_id_fields = ("created_by",)
    inlines = (SpotTagInline,)
    ordering = ("-created_at",)

    @admin.display(description=_("ID"))
    def short_id(self, obj: Spot) -> str:
        return str(obj.id)[:8] + "…"

    @admin.display(description=_("Metrics"))
    def metrics_summary(self, obj: Spot) -> str:
        if not hasattr(obj, "metrics"):
            return "—"
        m = obj.metrics
        return f"{m.open_votes} open / {m.not_sure_votes} unsure · score {m.weighted_score:.2f}"

    @admin.display(description=_("Metrics (read-only)"))
    def metrics_readonly_block(self, obj: Spot) -> str:
        if not obj.pk or not hasattr(obj, "metrics"):
            return _("Save the spot first; metrics appear after votes or recompute.")
        m = obj.metrics
        return format_html(
            "<table><tbody>"
            "<tr><th>open_votes</th><td>{}</td></tr>"
            "<tr><th>not_sure_votes</th><td>{}</td></tr>"
            "<tr><th>weighted_score</th><td>{}</td></tr>"
            "<tr><th>activity_score</th><td>{}</td></tr>"
            "<tr><th>last_confirmed_at</th><td>{}</td></tr>"
            "<tr><th>recent_confirmations</th><td>{}</td></tr>"
            "</tbody></table>",
            m.open_votes,
            m.not_sure_votes,
            m.weighted_score,
            m.activity_score,
            m.last_confirmed_at or "—",
            m.recent_confirmations,
        )


@admin.register(SpotTag)
class SpotTagAdmin(admin.ModelAdmin):
    list_display = ("tag", "spot", "added_by")
    list_filter = ("tag",)
    search_fields = ("tag", "spot__name", "spot__id")
    raw_id_fields = ("spot", "added_by")


@admin.register(SpotVote)
class SpotVoteAdmin(admin.ModelAdmin):
    list_display = ("id", "spot", "device", "vote_type", "weight", "created_at")
    list_filter = ("vote_type", "created_at")
    search_fields = ("spot__name", "spot__id", "device__device_id")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("spot", "device")
    ordering = ("-created_at",)


@admin.register(SpotNote)
class SpotNoteAdmin(admin.ModelAdmin):
    list_display = ("short_text", "spot", "device", "is_flagged", "created_at")
    list_filter = ("is_flagged", "created_at")
    search_fields = ("text", "spot__name", "device__device_id")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("spot", "device")
    ordering = ("-created_at",)

    @admin.display(description=_("Text"))
    def short_text(self, obj: SpotNote) -> str:
        t = obj.text.strip()
        return t[:80] + ("…" if len(t) > 80 else "")


@admin.register(SpotMetrics)
class SpotMetricsAdmin(admin.ModelAdmin):
    list_display = (
        "spot",
        "open_votes",
        "not_sure_votes",
        "weighted_score",
        "activity_score",
        "last_confirmed_at",
    )
    list_filter = ("last_confirmed_at",)
    search_fields = ("spot__name", "spot__id")
    raw_id_fields = ("spot",)
    readonly_fields = (
        "spot",
        "open_votes",
        "not_sure_votes",
        "weighted_score",
        "activity_score",
        "last_confirmed_at",
        "recent_confirmations",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(EngagementEvent)
class EngagementEventAdmin(admin.ModelAdmin):
    list_display = ("type", "device", "created_at")
    list_filter = ("type", "created_at")
    search_fields = ("type", "device__device_id")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("device",)
    ordering = ("-created_at",)
