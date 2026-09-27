from datetime import date

from django.db import migrations

# CLAUDE.md §7: CTO conversion multipliers, per CSC-DBM Joint Circular No. 2,
# s.2004, as carried into BDH's approved Dec 16, 2025 HR Policy Revision
# Proposal on CTO and Exchange of Duty (the same policy §7 and §8 cite).
SEED_EFFECTIVE_DATE = date(2025, 12, 16)
SEED_WEEKDAY_MULTIPLIER = "1.00"
SEED_RESTDAY_HOLIDAY_MULTIPLIER = "1.50"


def seed_rate(apps, schema_editor):
    CTOMultiplierRate = apps.get_model("cto", "CTOMultiplierRate")
    CTOMultiplierRate.objects.get_or_create(
        effective_date=SEED_EFFECTIVE_DATE,
        defaults=dict(
            weekday_multiplier=SEED_WEEKDAY_MULTIPLIER,
            restday_holiday_multiplier=SEED_RESTDAY_HOLIDAY_MULTIPLIER,
            notes="BDH HR Policy Revision Proposal on CTO and Exchange of Duty, approved Dec 16, 2025.",
        ),
    )


def unseed_rate(apps, schema_editor):
    CTOMultiplierRate = apps.get_model("cto", "CTOMultiplierRate")
    CTOMultiplierRate.objects.filter(effective_date=SEED_EFFECTIVE_DATE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cto", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_rate, unseed_rate),
    ]
