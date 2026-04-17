import uuid

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Device",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("device_id", models.CharField(max_length=255, unique=True)),
                ("karma", models.IntegerField(default=0)),
                ("trust_score", models.FloatField(default=0.0)),
                ("last_active_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="Spot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(blank=True, max_length=255, null=True)),
                ("latitude", models.FloatField(db_index=True)),
                ("longitude", models.FloatField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "created_by",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="spots", to="core.device"),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [models.Index(fields=["latitude", "longitude"], name="core_spot_latitud_1f0c6b_idx"), models.Index(fields=["created_at"], name="core_spot_created_4ece35_idx")],
            },
        ),
        migrations.CreateModel(
            name="SpotMetrics",
            fields=[
                ("open_votes", models.IntegerField(default=0)),
                ("not_sure_votes", models.IntegerField(default=0)),
                ("weighted_score", models.FloatField(default=0.0)),
                ("activity_score", models.FloatField(default=0.0)),
                ("last_confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("recent_confirmations", models.IntegerField(default=0)),
                (
                    "spot",
                    models.OneToOneField(on_delete=models.deletion.CASCADE, primary_key=True, related_name="metrics", serialize=False, to="core.spot"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SpotNote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("text", models.TextField()),
                ("is_flagged", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "device",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="notes", to="core.device"),
                ),
                (
                    "spot",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="notes", to="core.spot"),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["spot", "created_at"], name="core_spotno_spot_id_8bb620_idx"),
                    models.Index(fields=["device", "created_at"], name="core_spotno_device__7c8410_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SpotTag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tag", models.CharField(db_index=True, max_length=64)),
                (
                    "spot",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="tags", to="core.spot"),
                ),
            ],
            options={},
        ),
        migrations.CreateModel(
            name="SpotVote",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("vote_type", models.CharField(choices=[("OPEN", "Open"), ("NOT_SURE", "Not Sure")], db_index=True, max_length=16)),
                ("weight", models.FloatField(default=1.0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "device",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="votes", to="core.device"),
                ),
                (
                    "spot",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="votes", to="core.spot"),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["spot", "created_at"], name="core_spotvo_spot_id_221711_idx"),
                    models.Index(fields=["device", "created_at"], name="core_spotvo_device__de1af7_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="EngagementEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("type", models.CharField(db_index=True, max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="engagement_events",
                        to="core.device",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["device", "type", "created_at"], name="core_engage_device__24ca95_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="spottag",
            constraint=models.UniqueConstraint(fields=("spot", "tag"), name="unique_spot_tag"),
        ),
        migrations.AddConstraint(
            model_name="spotvote",
            constraint=models.UniqueConstraint(
                fields=("spot", "device", "vote_type"),
                name="unique_vote_per_device_per_type",
            ),
        ),
    ]
