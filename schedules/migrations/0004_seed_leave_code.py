"""Pilot Item 6: the LV code (on leave). Added only if HR has not made one."""

from decimal import Decimal

from django.db import migrations


def seed(apps, schema_editor):
    ShiftCode = apps.get_model("schedules", "ShiftCode")
    ShiftCode.objects.get_or_create(code="LV", defaults=dict(
        description="On leave or CTO (needs a leave or CTO request)", paid_hours=Decimal("0"), is_duty=False,
        sort_order=8))


class Migration(migrations.Migration):
    dependencies = [("schedules", "0003_schedule_cutoff")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
