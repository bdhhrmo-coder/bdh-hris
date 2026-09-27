"""
Document upload system — CLAUDE.md §10.

One generic system, not a separate attachment table per app: a
GenericForeignKey lets UploadedDocument attach to any transaction model
(LeaveApplication, CTOUsageApplication, CTOCreditEntry, DutyExchangeRequest,
AttendanceCorrectionRequest, OfficialRequest) without touching those apps'
own tables.

Confirmed with the project owner on 2026-09-27:
  - Confidential documents (medical certificates, government IDs, and
    anything else flagged confidential) are visible to the employee who
    owns the transaction, HR (Processor/Administrator), and System
    Administrator only — a Supervisor/AO/COH acting on that transaction
    sees that a required document was submitted, not the file itself.
  - Only one specific document requirement was confirmed as real BDH
    policy at build time: CTO claims need the Allowed to Work form,
    DTR/logbook copy, and OT Accomplishment Report (§7) for regular
    OT/rest-day/holiday duty, OR — for Medical Transport ("Decking
    schedule") duty, any section — a Trip Ticket plus either a
    Certificate of Appearance or a logbook copy (this fully replaces the
    regular set, it doesn't add to it). §10's "e.g. medical certificate
    for Sick Leave" was introduced only as an illustration of the
    configurability concept, not confirmed as actual policy, so nothing
    is seeded for Sick Leave — the table stays configurable in Django
    admin for HR to add that (or any other) requirement when confirmed.

Scope boundary (documented so it isn't silently assumed later): this
phase builds the upload system, validation, secure storage, audit trail,
and access control, and surfaces which configured requirements are
met/missing for a transaction. It does NOT gate any routing action in
leave/cto/exchange/attendance/official_requests on a missing document —
CLAUDE.md's Build Order scopes Phase 9 as the document system itself, and
wiring "block approval until required docs are uploaded" into five other
apps' approval logic is a further, separate decision.
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models


def secure_upload_path(instance, filename):
    """Never store the original filename as (or in) the on-disk path —
    CLAUDE.md §10: 'uploaded files must be automatically renamed/secured
    on storage.' The original name is kept separately, for display only."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    new_name = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
    return f"secure_documents/{new_name}"


class DocumentRequirement(models.Model):
    """
    A configurable "this transaction type needs this document" rule —
    CLAUDE.md §10: "document requirements should be configurable per
    transaction type." Kept in the database (not hardcoded), the same way
    CTO multipliers and biometric column mappings are.

    content_type identifies WHICH model this applies to (e.g.
    cto.CTOCreditEntry). sub_type_value optionally narrows it to a
    specific value of that model's own "kind" field (e.g. duty_type for
    CTOCreditEntry, leave_type.code for LeaveApplication, request_type for
    OfficialRequest, correction_type for AttendanceCorrectionRequest) —
    see documents/requirements.py's SUBTYPE_EXTRACTORS for the mapping.
    Blank sub_type_value means "applies to every instance of this model."

    alternative_group lets two requirements stand in for each other (e.g.
    Certificate of Appearance OR a logbook copy) — requirements sharing a
    non-blank group are satisfied as a set by uploading any ONE of them.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    sub_type_value = models.CharField(
        max_length=30, blank=True,
        help_text="Leave blank to apply to every instance of this model, or give the exact value of "
        "that model's own 'kind' field (e.g. MEDICAL_TRANSPORT, SL, TRAVEL) to narrow it.",
    )
    alternative_group = models.CharField(
        max_length=50, blank=True,
        help_text="Requirements sharing the same non-blank group are alternatives to each other — "
        "uploading any ONE of them satisfies the whole group.",
    )
    label = models.CharField(max_length=150)
    is_mandatory = models.BooleanField(default=True)
    is_confidential = models.BooleanField(
        default=False, help_text="Medical certificates, government IDs, etc. — restricted per §10.",
    )

    class Meta:
        ordering = ["content_type", "sub_type_value", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "sub_type_value", "label"], name="unique_requirement_label_per_subtype"
            )
        ]

    def __str__(self):
        scope = self.sub_type_value or "all"
        return f"{self.content_type.model} [{scope}]: {self.label}"


class UploadedDocument(models.Model):
    PDF = "PDF"
    JPG = "JPG"
    PNG = "PNG"
    FILE_TYPE_CHOICES = [(PDF, "PDF"), (JPG, "JPG/JPEG"), (PNG, "PNG")]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    requirement = models.ForeignKey(
        DocumentRequirement, on_delete=models.SET_NULL, null=True, blank=True, related_name="uploads",
        help_text="Which configured requirement this satisfies, if any (blank for a supplementary attachment).",
    )
    file = models.FileField(upload_to=secure_upload_path, max_length=300)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="Bytes, captured at upload time.")
    file_type = models.CharField(max_length=5, choices=FILE_TYPE_CHOICES)
    is_confidential = models.BooleanField(
        default=False, help_text="Snapshotted from the requirement at upload time, or set directly for a "
        "supplementary confidential attachment.",
    )
    is_active = models.BooleanField(
        default=True, help_text="False once superseded by a replacement — kept, not deleted, for the audit trail.",
    )
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes"
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.original_filename} ({self.content_type.model} #{self.object_id})"

    def clean(self):
        if self.requirement_id and self.requirement.content_type_id != self.content_type_id:
            raise ValidationError("The chosen requirement doesn't belong to this document's transaction type.")


class UploadedDocumentEvent(models.Model):
    """Audit trail of uploads and replacements — CLAUDE.md §10: 'maintain
    an audit trail of uploads and replacements.'"""

    UPLOADED = "UPLOADED"
    REPLACED = "REPLACED"
    EVENT_TYPE_CHOICES = [(UPLOADED, "Uploaded"), (REPLACED, "Replaced an earlier document")]

    document = models.ForeignKey(UploadedDocument, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=10, choices=EVENT_TYPE_CHOICES)
    replaces = models.ForeignKey(
        UploadedDocument, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Set only for a REPLACED event: the document this one superseded.",
    )
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]

    def __str__(self):
        return f"{self.document} — {self.event_type} @ {self.acted_at:%Y-%m-%d %H:%M}"
