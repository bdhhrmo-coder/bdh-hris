from django.db import migrations

# Fixed section/unit list per CLAUDE.md §4. Kept admin-configurable (a plain
# table, not hardcoded choices), so this migration only seeds the starting
# data — HR/IT can rename or add sections later through the admin.
SECTIONS = [
    ("Medical Services Section", []),
    (
        "Nursing Service Section",
        ["OPD", "ER", "Isolation/Medical", "Pediatric", "OB/Surgical", "OR/DR"],
    ),
    ("Radiology Section", []),
    ("Laboratory Section", []),
    ("Pharmacy Section", []),
    ("Health Information Management Section", []),
    ("Medical Social Service Section", []),
    ("Accounting and Finance Section", []),
    ("Human Resources Management Section", []),
    ("General Services Section", []),
    ("Procurement, Property and Supply Section", []),
    ("Dietary Section", []),
]


def seed_sections(apps, schema_editor):
    Section = apps.get_model("orgstructure", "Section")
    Unit = apps.get_model("orgstructure", "Unit")
    for section_name, units in SECTIONS:
        section, _ = Section.objects.get_or_create(name=section_name)
        for unit_name in units:
            Unit.objects.get_or_create(section=section, name=unit_name)


def unseed_sections(apps, schema_editor):
    Section = apps.get_model("orgstructure", "Section")
    Section.objects.filter(name__in=[name for name, _ in SECTIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orgstructure", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_sections, unseed_sections),
    ]
