"""Position records from the titles already on employee records, and the
Exchange groups decided by the owner on 2026-11-26 (seeds: Nurse, Nursing
Attendant, Midwife; added: Medical Technologist, Radiologic Technologist,
Pharmacist). Every other position starts with no group. Changes nothing
else on the employee records."""

from django.db import migrations

GROUPS = {
    "Nurse": ["Nurse I", "Nurse II", "Nurse III", "Clinical Nurse", "ROS Clinical Nurse", "ROS Clinical Nurse II",
              "Nurse II/ Chief Nurse", "Nurse II/ HRMO Designate"],
    "Nursing Attendant": ["Nursing Attendant I", "Nursing Aide I", "Nursing Aide II", "Nursing Aide III"],
    "Midwife": ["Midwife II", "ROS Midwifery Practitioner III"],
    "Medical Technologist": ["Clinical Medical Technologist", "Medical Technologist II",
                             "ROS Clinical Medical Technologist I", "ROS Medical Technologist I"],
    "Radiologic Technologist": ["Radiologic Technologist I", "Radiologic Technologist II",
                                "Clinical Radiologic Technologist", "ROS Radiologic Technologist I",
                                "ROS Radiologic Technologist II", "ROS Radiologic Technologist III"],
    "Pharmacist": ["Pharmacist I", "Pharmacist II", "Clinical Pharmacist", "ROS Clinical Pharmacist I",
                   "ROS Clinical Pharmacist II", "ROS Clinical Pharmacist III"],
}


def key(title):
    return " ".join((title or "").split()).lower()


def forwards(apps, schema_editor):
    Employee = apps.get_model("employees", "Employee")
    Position = apps.get_model("employees", "Position")
    ExchangeGroup = apps.get_model("employees", "ExchangeGroup")

    def position(title):
        pos, _ = Position.objects.get_or_create(title_key=key(title), defaults={"title": " ".join(title.split())})
        return pos

    for emp in Employee.objects.exclude(position=""):
        Employee.objects.filter(pk=emp.pk).update(position_ref=position(emp.position))
    for name, titles in GROUPS.items():
        group, _ = ExchangeGroup.objects.get_or_create(name=name)
        for title in titles:
            pos = position(title)
            if pos.exchange_group_id is None:
                pos.exchange_group = group
                pos.save(update_fields=["exchange_group"])


class Migration(migrations.Migration):
    dependencies = [("employees", "0012_positions_exchange_groups")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
