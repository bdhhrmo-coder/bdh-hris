from django.db import migrations


def forwards(apps, schema_editor):
    from performance.seed_data import load

    load(apps)


class Migration(migrations.Migration):
    dependencies = [("performance", "0001_initial"), ("orgstructure", "0002_seed_sections")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
