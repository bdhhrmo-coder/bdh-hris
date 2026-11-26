"""Item 6 / audit fix F6 (owner Decision 10, 2026-11-26): LV is for leave days and CTO for CTO days.
Only the seeded descriptions are updated; a description HR has changed is left alone."""

from django.db import migrations

OLD = {"LV": "On leave (needs a leave or CTO request)", "CTO": "On CTO (needs a leave or CTO request)"}
NEW = {"LV": "On leave (needs a leave request)", "CTO": "On CTO (needs a CTO request)"}


def forward(apps, schema_editor):
    ShiftCode = apps.get_model("schedules", "ShiftCode")
    for code, text in NEW.items():
        ShiftCode.objects.filter(code=code, description=OLD[code]).update(description=text)


def backward(apps, schema_editor):
    ShiftCode = apps.get_model("schedules", "ShiftCode")
    for code, text in OLD.items():
        ShiftCode.objects.filter(code=code, description=NEW[code]).update(description=text)


class Migration(migrations.Migration):
    dependencies = [("schedules", "0004_seed_leave_code")]
    operations = [migrations.RunPython(forward, backward)]
