from __future__ import annotations

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_spottag_added_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="spotmetrics",
            name="typical_close_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="spotmetrics",
            name="open_probability",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="spotmetrics",
            name="reliability_score",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="spotmetrics",
            name="total_confirmations",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="spotmetrics",
            name="unique_contributors",
            field=models.IntegerField(default=0),
        ),
    ]
