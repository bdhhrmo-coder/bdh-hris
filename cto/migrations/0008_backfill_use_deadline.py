from django.db import migrations


def backfill(apps, schema_editor):
    """Lines credited before use_deadline existed (dry-run-1 builds) get the
    deadline they would have got when credited."""
    from cto.carryover import fill_missing_use_deadlines

    fill_missing_use_deadlines(apps.get_model("cto", "CTOClaimLine"))


class Migration(migrations.Migration):
    dependencies = [("cto", "0007_carryover_and_line_deadlines")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
