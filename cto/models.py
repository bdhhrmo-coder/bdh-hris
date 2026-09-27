"""
CTO (Compensatory Time Off) models — CLAUDE.md §7. Flagged by CLAUDE.md as
the highest compliance-risk module in the whole build; see cto/balances.py
for the math this is built on top of, kept isolated and unit-tested the
same way leave/balances.py is.

Two separate things happen here, on purpose:
  - CREDITING (earning CTO from OT/restday/holiday work): recorded directly
    by HR for now. §7 describes this as a "claim...filed...with required
    attachments", which reads like the same thing as the "Authorized
    OT/restday/holiday work" request in §6.2's routing table — but that
    request workflow is explicitly Phase 8, not built yet. Rather than
    block the whole CTO engine on a phase that hasn't started, HR records
    a verified credit entry directly (confirmed 2026-09-27: no file
    upload yet either — Phase 9's job). Once Phase 8 exists, its approved
    OT/restday/holiday requests can create these entries instead of HR
    typing them in by hand; the ledger underneath doesn't change.
  - SPENDING (using banked CTO as time off): full routing chain per
    §6.2 — Employee -> Supervisor -> HR -> AO -> COH, same shape as COSP
    Leave in the leave app (and deliberately reuses leave.permissions'
    routing-role checks rather than re-implementing them).

Not applicable to the Chief of Hospital (§7): enforced structurally in
forms/views, not just left as a convention.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CTOMultiplierRate(models.Model):
    """
    OT-hours-to-CTO-hours multiplier, effective-dated so a future policy
    change never rewrites how a past claim was computed. CLAUDE.md §7:
    "Multipliers must be configurable, not hardcoded."

    Seeded from BDH's approved Dec 16, 2025 HR Policy Revision Proposal
    (the same one §7 cites): 1.0x weekday, 1.5x rest day/holiday, per
    CSC-DBM Joint Circular No. 2, s.2004.
    """

    effective_date = models.DateField(unique=True)
    weekday_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)
    restday_holiday_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1.5)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"CTO multipliers effective {self.effective_date}: {self.weekday_multiplier}x / {self.restday_holiday_multiplier}x"

    @classmethod
    def active_as_of(cls, as_of_date):
        return cls.objects.filter(effective_date__lte=as_of_date).order_by("-effective_date").first()


class CTOCreditEntry(models.Model):
    """
    One verified OT/rest-day/holiday work record, converted to CTO credit.
    Recorded by HR directly (see module docstring) — not itself routed
    through Supervisor/AO/COH; HR is trusted to have verified the
    attachments described in §7 against the employee's DTR/logbook before
    entering this.

    duty_type (added for Phase 9's document-requirements system, confirmed
    2026-09-27): the standard §7 attachment set (Allowed to Work form,
    DTR/logbook copy, OT Accomplishment Report) assumes regular OT/rest-
    day/holiday duty. Medical Transport ("Decking" schedule) duty follows a
    different, fully separate document set instead (Trip Ticket, plus
    either a Certificate of Appearance or a logbook copy) — regardless of
    which section the employee belongs to. See documents/requirements.py
    for how the two sets are configured.
    """

    DUTY_REGULAR = "REGULAR"
    DUTY_MEDICAL_TRANSPORT = "MEDICAL_TRANSPORT"
    DUTY_TYPE_CHOICES = [
        (DUTY_REGULAR, "Regular OT/rest day/holiday duty"),
        (DUTY_MEDICAL_TRANSPORT, "Medical Transport (Decking schedule)"),
    ]

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="cto_credit_entries"
    )
    work_date = models.DateField(help_text="The date the OT/rest-day/holiday work was performed.")
    duty_type = models.CharField(max_length=20, choices=DUTY_TYPE_CHOICES, default=DUTY_REGULAR)
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2)
    is_restday_or_holiday = models.BooleanField(
        default=False, help_text="Determines which multiplier applies (weekday vs rest day/holiday)."
    )
    shift_hours_used = models.PositiveSmallIntegerField(
        help_text="Snapshot of the employee's shift_hours at entry time, so a later shift-length "
        "change never silently reinterprets this entry's day conversion."
    )
    multiplier_applied = models.DecimalField(
        max_digits=4, decimal_places=2,
        help_text="Snapshot of the multiplier in effect on work_date — never recomputed later.",
    )
    credited_hours = models.DecimalField(max_digits=6, decimal_places=2)
    credited_days = models.DecimalField(max_digits=6, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-work_date"]

    def __str__(self):
        return f"{self.employee} worked {self.hours_worked}h on {self.work_date} -> {self.credited_days} CTO day(s)"

    def clean(self):
        if self.employee_id and self.employee.has_role("CHIEF_OF_HOSPITAL"):
            raise ValidationError("CTO is not applicable to the Chief of Hospital (CLAUDE.md §7).")


class CTOCreditTransaction(models.Model):
    """
    Append-only CTO balance ledger — same pattern as
    leave.LeaveCreditTransaction, for the same reason: a running balance
    field would be a black box, this is traceable.
    """

    EARNED = "EARNED"
    USED = "USED"
    ADJUSTMENT = "ADJUSTMENT"
    FORFEITED = "FORFEITED"
    TRANSACTION_TYPE_CHOICES = [
        (EARNED, "Earned/credited"),
        (USED, "Used"),
        (ADJUSTMENT, "Manual adjustment"),
        (FORFEITED, "Forfeited"),
    ]

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="cto_credit_transactions"
    )
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPE_CHOICES)
    days = models.DecimalField(
        max_digits=6, decimal_places=2, help_text="Positive for a credit, negative for a deduction."
    )
    transaction_date = models.DateField()
    credit_entry = models.ForeignKey(
        CTOCreditEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries"
    )
    usage_application = models.ForeignKey(
        "CTOUsageApplication", on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries"
    )
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-id"]

    def __str__(self):
        return f"{self.employee} — CTO {self.days:+} ({self.transaction_date})"


class CTOUsageApplication(models.Model):
    """
    An employee spending banked CTO credit as time off. Always full
    routing (§6.2: "CTO Application (regular & COSP): Employee ->
    Supervisor -> HR -> AO -> COH") — unlike leave.LeaveApplication,
    there's no data-entry-only path here, so this ends at APPROVED rather
    than ever reaching a RECORDED-only status.
    """

    SUBMITTED = "SUBMITTED"
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"
    PROCESSED_BY_HR = "PROCESSED_BY_HR"
    RECOMMENDED_BY_AO = "RECOMMENDED_BY_AO"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (RECOMMENDED_BY_AO, "Recommended by AO"),
        (APPROVED, "Approved"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]
    TERMINAL_STATUSES = {APPROVED, REJECTED, CANCELLED}

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="cto_usage_applications"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    number_of_days = models.DecimalField(max_digits=4, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=SUBMITTED)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.employee} — CTO {self.start_date} to {self.end_date} ({self.status})"

    def clean(self):
        if self.employee_id and self.employee.has_role("CHIEF_OF_HOSPITAL"):
            raise ValidationError("CTO is not applicable to the Chief of Hospital (CLAUDE.md §7).")


class CTOUsageApplicationAction(models.Model):
    """Accountability log per routing step — same shape as LeaveApplicationAction."""

    application = models.ForeignKey(CTOUsageApplication, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=CTOUsageApplication.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]

    def __str__(self):
        return f"{self.application} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"
