from __future__ import annotations

import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_spotusualtiming"),
    ]

    operations = [
        migrations.AddField(
            model_name="spottag",
            name="added_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="spot_tags_added",
                to="core.device",
            ),
        ),
    ]
