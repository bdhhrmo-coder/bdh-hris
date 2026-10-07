"""Second half of the multi-date change (Batch 2, Item 6): the single-date
fields were copied onto AttendanceCorrectionLine in 0004. Kept as a
separate migration so PostgreSQL runs the data copy and the column drops in
separate transactions."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_correction_lines"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="attendancecorrectionrequest",
            name="date",
        ),
        migrations.RemoveField(
            model_name="attendancecorrectionrequest",
            name="reason",
        ),
        migrations.RemoveField(
            model_name="attendancecorrectionrequest",
            name="reason_category",
        ),
        migrations.RemoveField(
            model_name="attendancecorrectionrequest",
            name="requested_is_absent",
        ),
        migrations.RemoveField(
            model_name="attendancecorrectionrequest",
            name="requested_time_in",
        ),
        migrations.RemoveField(
            model_name="attendancecorrectionrequest",
            name="requested_time_out",
        ),
    ]
