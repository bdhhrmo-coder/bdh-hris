"""
RA 10173 (Data Privacy Act) audit-log/data-retention app — CLAUDE.md §13,
built only after the project owner's explicit go-ahead (2026-09-27).

Deliberately holds only ONE new model. The "audit log" itself is NOT a new
table: every routed-request app already keeps its own accountability
record per transition —

    leave.LeaveApplicationAction
    cto.CTOUsageApplicationAction
    exchange.DutyExchangeRequestAction
    attendance.AttendanceCorrectionRequestAction
    official_requests.OfficialRequestAction
    documents.UploadedDocumentEvent
    employees.EmployeeEditHistory

— each recording who did what, when, and (where applicable) the resulting
status or before/after values. Duplicating that into a second store would
violate the "one reliable source of information" rule this project is
built on, and would let the two copies drift apart. What was actually
missing, and what this app adds, is:

  1. A single, role-gated, filterable VIEW across all seven sources
     (auditlog/aggregation.py) — so HR Administrator/AO/COH/System
     Administrator can produce a hospital-wide audit trail on demand
     without opening seven different Django-admin screens.
  2. A personnel-record RETENTION REVIEW (auditlog/retention.py), per the
     project owner's 10-year retention period for separated employees'
     records (confirmed 2026-09-27). This is flag-and-decide, never
     auto-delete — CLAUDE.md §6 "Preserve Human Control" and the existing
     backup-retention note ("manual cleanup ... no auto-delete logic
     needed") both point the same way. The system only ever calculates
     the due date and records the human decision below; nothing in this
     app deletes, purges, or archives a record.
"""

from django.conf import settings
from django.db import models


class RetentionReviewRecord(models.Model):
    """
    One row per retention-review decision made about a separated
    employee's personnel record. Recording a decision here does not
    change or remove any data — it is only the accountability record for
    a human decision, same spirit as every *Action model above.

    A DISPOSE decision means HR/AO/COH decided the record may be
    archived/destroyed per BDH's records disposal procedure; carrying
    that out (and any required National Archives of the Philippines
    clearance) happens outside this system.
    """

    RETAIN = "RETAIN"
    DISPOSE = "DISPOSE"
    DECISION_CHOICES = [
        (RETAIN, "Retain — do not flag again until manually re-reviewed"),
        (DISPOSE, "Approved for disposal per records retention schedule"),
    ]

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="retention_reviews"
    )
    decision = models.CharField(max_length=10, choices=DECISION_CHOICES)
    reason = models.TextField(help_text="Required. Basis for the decision.")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="retention_reviews_made"
    )
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-reviewed_at"]

    def __str__(self):
        return f"{self.employee} — {self.decision} @ {self.reviewed_at:%Y-%m-%d}"
