from datetime import time
from decimal import Decimal

from django.db import migrations

SEED = [
    ("8-5", "Regular day, lunch 12:00NN-1:00PM", time(8), time(17), Decimal("8"), True, 1),
    ("7A-7P", "12-hour day", time(7), time(19), Decimal("12"), True, 2),
    ("7P-7A", "12-hour night (ends next day)", time(19), time(7), Decimal("12"), True, 3),
    ("OFF", "No duty", None, None, Decimal("0"), False, 9),
]


def seed(apps, schema_editor):
    ShiftCode = apps.get_model("schedules", "ShiftCode")
    for code, desc, start, end, hours, duty, order in SEED:
        ShiftCode.objects.get_or_create(code=code, defaults=dict(
            description=desc, start_time=start, end_time=end, paid_hours=hours, is_duty=duty, sort_order=order))


class Migration(migrations.Migration):
    dependencies = [("schedules", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
