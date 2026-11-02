from django.db import migrations


def move(apps, schema_editor, old, new):
    ContentType = apps.get_model("contenttypes", "ContentType")
    DocumentRequirement = apps.get_model("documents", "DocumentRequirement")
    old_ct, _ = ContentType.objects.get_or_create(app_label="cto", model=old)
    new_ct, _ = ContentType.objects.get_or_create(app_label="cto", model=new)
    DocumentRequirement.objects.filter(content_type=old_ct).update(content_type=new_ct)


class Migration(migrations.Migration):
    """CTO document requirements now attach to the claim (cto.CTOClaim),
    which replaced cto.CTOCreditEntry on 2026-11-01. Same REGULAR /
    MEDICAL_TRANSPORT sets, keyed by CTOClaim.duty_type."""

    dependencies = [
        ("documents", "0002_seed_cto_document_requirements"),
        ("cto", "0006_ot_cto_claims"),
    ]

    operations = [
        migrations.RunPython(
            lambda apps, se: move(apps, se, "ctocreditentry", "ctoclaim"),
            lambda apps, se: move(apps, se, "ctoclaim", "ctocreditentry"),
        ),
    ]
