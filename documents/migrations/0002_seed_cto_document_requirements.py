from django.db import migrations

# CLAUDE.md §7, confirmed 2026-09-27: regular OT/rest-day/holiday CTO
# claims need these three; a Medical Transport ("Decking schedule") claim
# — any section — needs a fully different set instead (Trip Ticket plus
# either a Certificate of Appearance or a logbook copy).
REGULAR_DOCS = ["Allowed to Work form", "DTR/logbook copy", "OT Accomplishment Report"]
MEDICAL_TRANSPORT_STANDALONE = ["Trip Ticket (with employee's name on it)"]
MEDICAL_TRANSPORT_ALTERNATIVES = ["Certificate of Appearance", "Logbook copy"]


def seed_requirements(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    DocumentRequirement = apps.get_model("documents", "DocumentRequirement")

    # Note: on a fresh test database, contenttypes' post_migrate handler
    # only runs after the *whole* migration plan finishes, not after each
    # app — so the ContentType row for cto.CTOCreditEntry is not guaranteed
    # to exist yet at this point. get_or_create rather than get.
    content_type, _ = ContentType.objects.get_or_create(app_label="cto", model="ctocreditentry")

    for label in REGULAR_DOCS:
        DocumentRequirement.objects.get_or_create(
            content_type=content_type, sub_type_value="REGULAR", label=label,
            defaults=dict(is_mandatory=True, is_confidential=False),
        )

    for label in MEDICAL_TRANSPORT_STANDALONE:
        DocumentRequirement.objects.get_or_create(
            content_type=content_type, sub_type_value="MEDICAL_TRANSPORT", label=label,
            defaults=dict(is_mandatory=True, is_confidential=False),
        )

    for label in MEDICAL_TRANSPORT_ALTERNATIVES:
        DocumentRequirement.objects.get_or_create(
            content_type=content_type, sub_type_value="MEDICAL_TRANSPORT", label=label,
            defaults=dict(is_mandatory=True, is_confidential=False, alternative_group="cert_or_logbook"),
        )


def unseed_requirements(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    DocumentRequirement = apps.get_model("documents", "DocumentRequirement")
    content_type, _ = ContentType.objects.get_or_create(app_label="cto", model="ctocreditentry")
    DocumentRequirement.objects.filter(
        content_type=content_type, sub_type_value__in=["REGULAR", "MEDICAL_TRANSPORT"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
        ("cto", "0003_ctocreditentry_duty_type"),
    ]

    operations = [
        migrations.RunPython(seed_requirements, unseed_requirements),
    ]
