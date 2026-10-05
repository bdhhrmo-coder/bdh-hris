"""
CTO (Compensatory Time Off) models — CLAUDE.md §7. Flagged by CLAUDE.md as
the highest compliance-risk module in the whole build; see cto/balances.py
for the math this is built on top of, kept isolated and unit-tested the
same way leave/balances.py is.

Two separate things happen here, on purpose:
  - CREDITING (earning CTO from OT/restday/holiday work): a CTO claim
    (CTOCreditEntry), filed by HR from an APPROVED "Authorized OT/restday/
    holiday work" request (official_requests.OfficialRequest). Settled
    2026-09-28 (see CLAUDE.md §7): approving OT never creates CTO credit
    by itself. The OT approval and the claim are separate records; the
    claim links back to the OT (ot_request) for the audit trail. A claim
    starts as a DRAFT so its required documents can be attached, and only
    credits the ledger when HR submits it and every §7 check passes (see
    views.claim_submit). One claim per workday; the hours across all of
    one OT's claims cannot exceed the OT's approved hours. An HR
    Administrator may also file an exception claim with no OT request
    (e.g. certified emergency or Medical Transport duty), with a required
    reason — it goes through the same draft/documents/submit checks.
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
    A CTO claim: one workday of OT/rest-day/holiday work, converted to CTO
    credit once submitted (see the module docstring). Normally linked to
    an approved OT request; exception claims have no ot_request and must
    give an exception_reason instead.

    multiplier_applied/credited_hours/credited_days/shift_hours_used are
    computed when the draft is saved (as a preview) and recomputed at
    submission, which is the snapshot that is actually credited.

    duty_type (added for Phase 9's document-requirements system, confirmed
    2026-09-27): the standard §7 attachment set (Allowed to Work form,
    DTR/logbook copy, OT Accomplishment Report) assumes regular OT/rest-
    day/holiday duty. Medical Transport ("Decking" schedule) duty follows a
    different, fully separate document set instead (Trip Ticket, plus
    either a Certificate of Appearance or a logbook copy) — regardless of
    which section the employee belongs to. See documents/requirements.py
    for how the two sets are configured.
    """

    DRAFT = "DRAFT"
    CREDITED = "CREDITED"
    STATUS_CHOICES = [(DRAFT, "Draft — not yet credited"), (CREDITED, "Credited")]

    DUTY_REGULAR = "REGULAR"
    DUTY_MEDICAL_TRANSPORT = "MEDICAL_TRANSPORT"
    DUTY_TYPE_CHOICES = [
        (DUTY_REGULAR, "Regular OT/rest day/holiday duty"),
        (DUTY_MEDICAL_TRANSPORT, "Medical Transport (Decking schedule)"),
    ]

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="cto_credit_entries"
    )
    ot_request = models.ForeignKey(
        "official_requests.OfficialRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cto_claims",
        help_text="The approved OT/rest-day/holiday request this claim is filed from. "
        "Blank only for an HR Administrator exception claim.",
    )
    exception_reason = models.TextField(
        blank=True, help_text="Required when there is no OT request (exception claim)."
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
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
    credited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    credited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-work_date"]
        constraints = [
            models.UniqueConstraint(fields=["ot_request", "work_date"], name="one_cto_claim_per_ot_workday"),
        ]

    def __str__(self):
        return f"{self.employee} worked {self.hours_worked}h on {self.work_date} -> {self.credited_days} CTO day(s)"

    def is_exception(self):
        return self.ot_request_id is None

    def clean(self):
        if self.employee_id and self.employee.has_role("CHIEF_OF_HOSPITAL"):
            raise ValidationError("CTO is not applicable to the Chief of Hospital (CLAUDE.md §7).")
        if self.ot_request_id is None:
            if not self.exception_reason.strip():
                raise ValidationError({"exception_reason": "A reason is required for a claim with no OT request."})
            return
        ot = self.ot_request
        if ot.request_type != ot.OT_RESTDAY_HOLIDAY or ot.status != ot.APPROVED:
            raise ValidationError("A CTO claim can only be filed from an approved OT/rest-day/holiday request.")
        if self.employee_id != ot.employee_id:
            raise ValidationError("The claim must be for the same employee as the OT request.")
        if self.work_date and not (ot.start_date <= self.work_date <= ot.end_date):
            raise ValidationError(
                {"work_date": f"The work date must fall within the OT request ({ot.start_date} to {ot.end_date})."}
            )


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
