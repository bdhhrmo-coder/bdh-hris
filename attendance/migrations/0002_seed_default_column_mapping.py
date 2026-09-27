from django.db import migrations

# CLAUDE.md §9: real column layout for BDH's biometric export wasn't known
# at build time (2026-09-27) — this default is a placeholder HR/IT should
# repoint at the real export via Django admin once a sample is available.
DEFAULT_MAPPING_NAME = "Default (placeholder — confirm against a real export)"


def seed_mapping(apps, schema_editor):
    BiometricColumnMapping = apps.get_model("attendance", "BiometricColumnMapping")
    BiometricColumnMapping.objects.get_or_create(
        name=DEFAULT_MAPPING_NAME,
        defaults=dict(
            employee_id_column="Employee ID",
            date_column="Date",
            time_in_column="Time In",
            time_out_column="Time Out",
            date_format="%Y-%m-%d",
            time_format="%H:%M:%S",
            is_active=True,
        ),
    )


def unseed_mapping(apps, schema_editor):
    BiometricColumnMapping = apps.get_model("attendance", "BiometricColumnMapping")
    BiometricColumnMapping.objects.filter(name=DEFAULT_MAPPING_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_mapping, unseed_mapping),
    ]
