"""The 16 personnel action types BDH HR uses (owner, 2026-11-26; ORAOHRA
Sec. 11 and Sec. 3 terms plus BDH's COSP and go-live entries). Adds only
names that are missing; HR may rename or switch any off."""

from django.db import migrations

NAMES = ["Original", "Promotion", "Transfer", "Reemployment", "Reappointment",
         "Reinstatement (to comparable position)", "Demotion", "Reclassification", "Reassignment", "Detail",
         "Secondment", "Designation", "COSP engagement", "COSP renewal", "Separation", "Opening line (go-live)"]


def forwards(apps, schema_editor):
    T = apps.get_model("employees", "PersonnelActionType")
    for i, name in enumerate(NAMES, start=1):
        T.objects.get_or_create(name=name, defaults={"sort_order": i * 10})


class Migration(migrations.Migration):
    dependencies = [("employees", "0014_holidays_assignment_history")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
