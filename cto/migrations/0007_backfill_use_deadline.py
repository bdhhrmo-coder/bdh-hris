import datetime

from django.conf import settings
from django.db import migrations


def fill_missing_use_deadlines(apps, schema_editor):
    """Credited lines without a use_deadline get the one they would have got
    when credited: Dec 15 of the year earned, or the next Dec 15 when the
    line took effect after that date. Self-contained on purpose (no import
    from cto.carryover), so later code changes cannot alter this migration."""
    CTOClaimLine = apps.get_model("cto", "CTOClaimLine")
    filled = 0
    month, day = settings.CTO_USAGE_CUTOFF_MONTH_DAY
    for line in CTOClaimLine.objects.filter(
        state__in=["CREDITED", "CARRY_PENDING"], use_deadline__isnull=True, effective_date__isnull=False
    ):
        year = line.year_earned or line.effective_date.year
        deadline = datetime.date(year, month, day)
        if line.effective_date > deadline:
            deadline = datetime.date(year + 1, month, day)
        line.use_deadline = deadline
        line.save(update_fields=["use_deadline"])
        filled += 1
    return filled  # RunPython ignores it; the test checks it


class Migration(migrations.Migration):
    dependencies = [("cto", "0006_ot_cto_claims")]

    operations = [migrations.RunPython(fill_missing_use_deadlines, migrations.RunPython.noop)]
