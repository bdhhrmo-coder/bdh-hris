from django.db import migrations

# Seed data for CLAUDE.md §6.1. Only VL, SL, WELLNESS, and COSP_LEAVE carry
# an explicit numeric cap here, because those are the only ones CLAUDE.md
# gives a number for. The rest are seeded as UNTRACKED (selectable and
# recorded, no system-enforced cap) rather than guessing statutory
# entitlement figures that were not confirmed for this build.
LEAVE_TYPES = [
    dict(
        code="VL", name="Vacation Leave", applicable_to="REGULAR",
        balance_tracking="ACCRUED", monthly_accrual_days="1.25",
    ),
    dict(
        code="SL", name="Sick Leave", applicable_to="REGULAR",
        balance_tracking="ACCRUED", monthly_accrual_days="1.25",
    ),
    dict(code="MANDATORY", name="Mandatory/Forced Leave", applicable_to="REGULAR"),
    dict(code="MATERNITY", name="Maternity Leave", applicable_to="REGULAR"),
    dict(code="PATERNITY", name="Paternity Leave", applicable_to="REGULAR"),
    dict(code="SPL", name="Special Privilege Leave", applicable_to="REGULAR"),
    dict(code="SOLO_PARENT", name="Solo Parent Leave", applicable_to="REGULAR"),
    dict(code="STUDY", name="Study Leave", applicable_to="REGULAR"),
    dict(code="VAWC", name="VAWC Leave", applicable_to="REGULAR"),
    dict(code="REHAB", name="Rehabilitation Privilege", applicable_to="REGULAR"),
    dict(code="SPECIAL_WOMEN", name="Special Leave Benefit for Women", applicable_to="REGULAR"),
    dict(code="CALAMITY", name="Calamity Leave", applicable_to="REGULAR"),
    dict(code="ADOPTION", name="Adoption Leave", applicable_to="REGULAR"),
    dict(
        code="WELLNESS", name="Wellness Leave", applicable_to="BOTH",
        balance_tracking="ANNUAL_CAP", annual_fixed_days="5", is_cumulative=False,
    ),
    dict(
        code="EMERGENCY", name="Emergency Leave", applicable_to="BOTH",
        requires_justification=True, is_cumulative=False,
    ),
    dict(
        code="COSP_LEAVE", name="COSP Leave", applicable_to="COSP",
        requires_full_routing=True, balance_tracking="ANNUAL_CAP",
        annual_fixed_days="20", is_cumulative=True,
    ),
]


def seed_leave_types(apps, schema_editor):
    LeaveType = apps.get_model("leave", "LeaveType")
    for entry in LEAVE_TYPES:
        data = dict(entry)
        code = data.pop("code")
        LeaveType.objects.get_or_create(code=code, defaults=data)


def unseed_leave_types(apps, schema_editor):
    LeaveType = apps.get_model("leave", "LeaveType")
    LeaveType.objects.filter(code__in=[d["code"] for d in LEAVE_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leave", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_leave_types, unseed_leave_types),
    ]
