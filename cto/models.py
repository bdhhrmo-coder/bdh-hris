"""
CTO (Compensatory Time Off) models — CLAUDE.md §7 / §7.1 (rules decided
2026-11-01, pending approved HR memo). Highest compliance-risk module; the
date/credit math lives in cto/balances.py so it can be unit-tested alone.

  - OvertimeRequest + OvertimeLine: authorized OT filed by a Supervisor
    (Unit Head in Nursing), HR or the AO for one or more employees, one
    line per employee per date. Approving OT never creates CTO credit.
  - CTOClaim + CTOClaimLine: the employee's claim against approved OT
    lines (or a Medical Transport / HR Admin exception claim). Credit,
    effective date and year earned are tracked per line.
  - CTOCreditAllocation: which credit line each CTO usage application
    draws from (oldest effective date first), held from filing.
  - CTOUsageApplication: spending CTO as time off (unchanged chain).

Routing for OT requests and claims is in cto/routing.py.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

WEEKDAY = "WEEKDAY"
REST_DAY = "REST_DAY"
HOLIDAY = "HOLIDAY"
DAY_TYPE_CHOICES = [(WEEKDAY, "Weekday"), (REST_DAY, "Rest day"), (HOLIDAY, "Holiday")]

# Routing step codes shared by OT requests and claims (cto/routing.py).
STEP_SUPERVISOR = "SUPERVISOR"
STEP_CHIEF_NURSE = "CHIEF_NURSE"
STEP_HR = "HR"
STEP_AO = "AO"
STEP_COH = "COH"
STEP_CHOICES = [
    (STEP_SUPERVISOR, "Supervisor / Unit Head"),
    (STEP_CHIEF_NURSE, "Chief Nurse"),
    (STEP_HR, "HR"),
    (STEP_AO, "Administrative Officer"),
    (STEP_COH, "Chief of Hospital"),
]


class CTOMultiplierRate(models.Model):
    """
    OT-hours-to-CTO-hours multiplier, effective-dated so a future policy
    change never rewrites how a past claim was computed. The rate in force
    on the WORKDAY applies (§7.1). Seeded 1.0x weekday / 1.5x rest day or
    holiday per CSC-DBM Joint Circular No. 2, s.2004.
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


class RoutedRecord(models.Model):
    """Shared status/route fields for OvertimeRequest and CTOClaim. `route`
    is the ordered step list snapshotted at submission (comma-separated
    STEP_* codes); `current_step` is the step now waiting to act."""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    RETURNED = "RETURNED"
    APPROVED = "APPROVED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (PENDING, "Pending approval"),
        (RETURNED, "Returned"),
        (APPROVED, "Approved"),
        (CANCELLED, "Cancelled"),
    ]
    EDITABLE_STATUSES = {DRAFT, RETURNED}

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    route = models.CharField(max_length=100, blank=True)
    current_step = models.CharField(max_length=15, choices=STEP_CHOICES, blank=True)
    approved_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def route_steps(self):
        return [s for s in self.route.split(",") if s]


class OvertimeRequest(RoutedRecord):
    """A batch of authorized OT lines, approved or returned as a whole."""

    CAPACITY_SUPERVISOR = "SUPERVISOR"
    CAPACITY_HR = "HR"
    CAPACITY_AO = "AO"
    CAPACITY_CHOICES = [
        (CAPACITY_SUPERVISOR, "Supervisor / Unit Head"),
        (CAPACITY_HR, "HR"),
        (CAPACITY_AO, "Administrative Officer"),
    ]

    filed_by = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="overtime_requests_filed"
    )
    filed_as = models.CharField(max_length=15, choices=CAPACITY_CHOICES)
    purpose = models.TextField()
    past_date_reason = models.TextField(blank=True, help_text="Required when any line is dated before filing.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"OT request #{self.pk} filed by {self.filed_by}"

    def get_absolute_url(self):
        return reverse("cto:ot_detail", args=[self.pk])

    def owner_employee(self):
        return self.filed_by

    def subject_employee_ids(self):
        """People who may not act on this batch: everyone on a line, plus
        the filer."""
        return set(self.lines.values_list("employee_id", flat=True)) | {self.filed_by_id}


class OvertimeLine(models.Model):
    request = models.ForeignKey(OvertimeRequest, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="overtime_lines")
    work_date = models.DateField()
    approved_hours = models.DecimalField(max_digits=5, decimal_places=2)
    day_type = models.CharField(max_length=10, choices=DAY_TYPE_CHOICES)

    class Meta:
        ordering = ["work_date", "employee"]
        constraints = [
            models.UniqueConstraint(fields=["request", "employee", "work_date"], name="one_ot_line_per_employee_date"),
        ]

    def __str__(self):
        return f"{self.employee} — {self.work_date} ({self.approved_hours}h {self.get_day_type_display()})"


class OvertimeRequestAction(models.Model):
    """Audit trail per OT request step (submit, approve, return, skip...)."""

    request = models.ForeignKey(OvertimeRequest, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    step = models.CharField(max_length=15, blank=True)
    notes = models.TextField(blank=True)
    acted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at", "id"]

    def __str__(self):
        return f"{self.request} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"


class CTOClaim(RoutedRecord):
    """
    An employee's CTO claim: one or more lines of their own, routed as one.
    duty_type picks the document set (documents/requirements.py): REGULAR =
    Allowed to Work form, DTR/logbook copy, OT Accomplishment Report;
    MEDICAL_TRANSPORT = Trip Ticket plus Certificate of Appearance or
    logbook. OT and Medical Transport lines never share a claim.
    """

    DUTY_REGULAR = "REGULAR"
    DUTY_MEDICAL_TRANSPORT = "MEDICAL_TRANSPORT"
    DUTY_TYPE_CHOICES = [
        (DUTY_REGULAR, "OT / rest day / holiday work"),
        (DUTY_MEDICAL_TRANSPORT, "Medical Transport (Decking schedule)"),
    ]

    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="cto_claims")
    duty_type = models.CharField(max_length=20, choices=DUTY_TYPE_CHOICES, default=DUTY_REGULAR)
    exception_reason = models.TextField(
        blank=True, help_text="HR Administrator exception claim (no OT request): required reason."
    )
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    first_filed_on = models.DateField(
        null=True, blank=True,
        help_text="Date of first submission. The Nov 30 filing deadline is judged on this date, "
        "so a returned claim does not expire while being corrected.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CTO claim #{self.pk} — {self.employee}"

    def is_exception(self):
        return bool(self.exception_reason.strip())

    def uses_ot_lines(self):
        return self.duty_type == self.DUTY_REGULAR and not self.is_exception()

    def get_absolute_url(self):
        return reverse("cto:claim_detail", args=[self.pk])

    def owner_employee(self):
        """Who fixes it when returned: the HR Administrator who filed an
        exception claim, otherwise the employee."""
        if self.is_exception():
            return getattr(self.filed_by, "employee", None)
        return self.employee

    def subject_employee_ids(self):
        return {self.employee_id}

    def clean(self):
        if self.employee_id and self.employee.has_role("CHIEF_OF_HOSPITAL"):
            raise ValidationError("CTO is not applicable to the Chief of Hospital (CLAUDE.md §7).")


class CTOClaimLine(models.Model):
    """One workday of a claim. credited_* is a preview until COH approval,
    when it is recomputed and effective_date/year_earned are set."""

    PENDING = "PENDING"
    CREDITED = "CREDITED"
    STATE_CHOICES = [(PENDING, "Not yet credited"), (CREDITED, "Credited")]

    claim = models.ForeignKey(CTOClaim, on_delete=models.CASCADE, related_name="lines")
    ot_line = models.ForeignKey(
        OvertimeLine, on_delete=models.PROTECT, null=True, blank=True, related_name="claim_lines",
        help_text="Blank for Medical Transport and exception lines.",
    )
    work_date = models.DateField()
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2)
    day_type = models.CharField(max_length=10, choices=DAY_TYPE_CHOICES)
    shift_hours_used = models.PositiveSmallIntegerField()
    multiplier_applied = models.DecimalField(max_digits=4, decimal_places=2)
    credited_hours = models.DecimalField(max_digits=6, decimal_places=2)
    credited_days = models.DecimalField(max_digits=6, decimal_places=2)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=PENDING)
    effective_date = models.DateField(null=True, blank=True)
    year_earned = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["work_date"]

    def __str__(self):
        return f"{self.claim.employee} — {self.work_date}: {self.hours_worked}h -> {self.credited_days} day(s)"


class CTOClaimAction(models.Model):
    """Audit trail per claim step, including day-type corrections and
    skipped steps."""

    claim = models.ForeignKey(CTOClaim, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    step = models.CharField(max_length=15, blank=True)
    notes = models.TextField(blank=True)
    acted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at", "id"]

    def __str__(self):
        return f"{self.claim} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"


class CTOUsageApplication(models.Model):
    """
    An employee spending CTO credit as time off. Full routing (§6.2):
    Employee -> Supervisor -> HR -> AO -> COH. Days are held against
    specific credit lines (CTOCreditAllocation) from filing; the hold is
    released if the application is returned, rejected or cancelled.
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


class CTOCreditAllocation(models.Model):
    """Days of one usage application drawn from one credit line."""

    line = models.ForeignKey(CTOClaimLine, on_delete=models.PROTECT, related_name="allocations")
    usage_application = models.ForeignKey(
        CTOUsageApplication, on_delete=models.CASCADE, related_name="allocations"
    )
    days = models.DecimalField(max_digits=6, decimal_places=2)

    def __str__(self):
        return f"{self.days} day(s) of {self.line} -> {self.usage_application}"


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
