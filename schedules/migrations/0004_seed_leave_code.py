"""Pilot Item 6: the LV (on leave) and CTO (on CTO) codes. Each added only if HR has not made one."""

from decimal import Decimal

from django.db import migrations


def seed(apps, schema_editor):
    ShiftCode = apps.get_model("schedules", "ShiftCode")
    ShiftCode.objects.get_or_create(code="LV", defaults=dict(
        description="On leave (needs a leave or CTO request)", paid_hours=Decimal("0"), is_duty=False,
        sort_order=8))
    ShiftCode.objects.get_or_create(code="CTO", defaults=dict(
        description="On CTO (needs a leave or CTO request)", paid_hours=Decimal("0"), is_duty=False,
        sort_order=9))


class Migration(migrations.Migration):
    dependencies = [("schedules", "0003_schedule_cutoff")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
