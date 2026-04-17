from django.db import migrations
from django.db import models


def dedupe_spot_votes(apps, schema_editor):  # noqa: ARG001
    SpotVote = apps.get_model("core", "SpotVote")
    duplicates = (
        SpotVote.objects.values("spot_id", "device_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )

    for row in duplicates.iterator():
        votes = list(
            SpotVote.objects.filter(
                spot_id=row["spot_id"],
                device_id=row["device_id"],
            ).order_by("-created_at", "-id"),
        )
        keep = votes[0]
        remove_ids = [vote.id for vote in votes[1:]]
        if remove_ids:
            SpotVote.objects.filter(id__in=remove_ids).delete()
        keep.save(update_fields=["vote_type", "weight"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_device_username_identity"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="spotvote",
            name="unique_vote_per_device_per_type",
        ),
        migrations.RunPython(dedupe_spot_votes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="spotvote",
            constraint=models.UniqueConstraint(
                fields=("spot", "device"),
                name="unique_vote_per_spot_per_device",
            ),
        ),
    ]
