from __future__ import annotations

import uuid

from django.db.models.functions import Lower
from django.db import models


class Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device_id = models.CharField(max_length=255, unique=True, db_index=True)
    username = models.CharField(max_length=64, unique=True, db_index=True)
    karma = models.IntegerField(default=0)
    trust_score = models.FloatField(default=0.0)
    last_active_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                Lower("username"),
                name="core_device_username_ci_unique",
            ),
        ]

    @staticmethod
    def normalize_username(value: str) -> str:
        return value.strip().lower()

    def save(self, *args, **kwargs):
        self.username = self.normalize_username(self.username)
        self.device_id = self.device_id.strip()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.username} ({self.device_id})"


class Spot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, null=True, blank=True)
    latitude = models.FloatField(db_index=True)
    longitude = models.FloatField(db_index=True)
    created_by = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="spots")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("latitude", "longitude")),
            models.Index(fields=("created_at",)),
        ]

    def __str__(self) -> str:
        return self.name or str(self.id)


class SpotTag(models.Model):
    spot = models.ForeignKey(Spot, on_delete=models.CASCADE, related_name="tags")
    tag = models.CharField(max_length=64, db_index=True)
    added_by = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="spot_tags_added",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("spot", "tag"), name="unique_spot_tag"),
        ]

    def __str__(self) -> str:
        return f"{self.spot_id}:{self.tag}"


class SpotVote(models.Model):
    class VoteType(models.TextChoices):
        OPEN = "OPEN", "Open"
        NOT_SURE = "NOT_SURE", "Not Sure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spot = models.ForeignKey(Spot, on_delete=models.CASCADE, related_name="votes")
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="votes")
    vote_type = models.CharField(max_length=16, choices=VoteType.choices, db_index=True)
    weight = models.FloatField(default=1.0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("spot", "device"),
                name="unique_vote_per_spot_per_device",
            ),
        ]
        indexes = [
            models.Index(fields=("spot", "created_at")),
            models.Index(fields=("device", "created_at")),
        ]


class SpotNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spot = models.ForeignKey(Spot, on_delete=models.CASCADE, related_name="notes")
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="notes")
    text = models.TextField()
    is_flagged = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=("spot", "created_at")),
            models.Index(fields=("device", "created_at")),
        ]


class SpotUsualTiming(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    spot = models.ForeignKey(Spot, on_delete=models.CASCADE, related_name="usual_timings")
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="usual_timings")
    usual_open_until = models.TimeField()
    weight = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("spot", "device"), name="unique_spot_timing_per_device"),
        ]
        indexes = [
            models.Index(fields=("spot", "updated_at")),
        ]


class SpotMetrics(models.Model):
    spot = models.OneToOneField(Spot, on_delete=models.CASCADE, related_name="metrics", primary_key=True)
    open_votes = models.IntegerField(default=0)
    not_sure_votes = models.IntegerField(default=0)
    weighted_score = models.FloatField(default=0.0)
    activity_score = models.FloatField(default=0.0)
    typical_close_time = models.TimeField(null=True, blank=True)
    open_probability = models.FloatField(default=0.0)
    reliability_score = models.FloatField(default=0.0)
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    total_confirmations = models.IntegerField(default=0)
    unique_contributors = models.IntegerField(default=0)
    recent_confirmations = models.IntegerField(default=0)


class EngagementEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(max_length=64, db_index=True)
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="engagement_events")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=("device", "type", "created_at")),
        ]
