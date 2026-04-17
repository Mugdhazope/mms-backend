from __future__ import annotations

from django.db import migrations
from django.db import models
from django.db.models.functions import Lower


def _normalize_username(value: str) -> str:
    return value.strip().lower()


def _default_username(device_id: str) -> str:
    base = _normalize_username(device_id) or "mms-user"
    return base[:20]


def backfill_usernames(apps, schema_editor):  # noqa: ARG001
    Device = apps.get_model("core", "Device")
    taken: set[str] = set(
        Device.objects.exclude(username__isnull=True)
        .exclude(username__exact="")
        .values_list("username", flat=True),
    )
    for device in Device.objects.all().iterator():
        existing = getattr(device, "username", None)
        if isinstance(existing, str) and existing.strip():
            normalized = _normalize_username(existing)
            if normalized == existing:
                taken.add(existing)
                continue
            device.username = normalized
            device.save(update_fields=["username"])
            taken.add(normalized)
            continue

        base = _default_username(device.device_id)
        candidate = base
        suffix = 1
        while candidate in taken:
            suffix_str = str(suffix)
            head = base[: max(1, 20 - len(suffix_str) - 1)]
            candidate = f"{head}-{suffix_str}"
            suffix += 1
        device.username = candidate
        device.save(update_fields=["username"])
        taken.add(candidate)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_spotmetrics_intelligence_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="username",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(backfill_usernames, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="device",
            name="username",
            field=models.CharField(db_index=True, max_length=64, unique=True),
        ),
        migrations.AddConstraint(
            model_name="device",
            constraint=models.UniqueConstraint(
                Lower("username"),
                name="core_device_username_ci_unique",
            ),
        ),
        migrations.AlterField(
            model_name="device",
            name="device_id",
            field=models.CharField(db_index=True, max_length=255, unique=True),
        ),
    ]
