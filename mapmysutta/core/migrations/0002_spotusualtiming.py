from __future__ import annotations

import uuid

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpotUsualTiming",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("usual_open_until", models.TimeField()),
                ("weight", models.FloatField(default=0.0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="usual_timings",
                        to="core.device",
                    ),
                ),
                (
                    "spot",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="usual_timings",
                        to="core.spot",
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["spot", "updated_at"], name="core_spotus_spot_id_756d16_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("spot", "device"), name="unique_spot_timing_per_device"),
                ],
            },
        ),
    ]
