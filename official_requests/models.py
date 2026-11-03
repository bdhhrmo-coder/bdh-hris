"""
Official Business, Official Time, Travel, and Authorized OT/restday/holiday
work requests — CLAUDE.md §6.2, Build Order Phase 8: "workflow routing
only, no special business rules yet."

One model, one routing engine (routing.py), four request_type values —
the same shape leave.LeaveType.requires_full_routing uses to split COSP
Leave from everything else, generalized here since three of these four
types share the identical Employee -> Supervisor -> HR -> AO -> COH chain
and the other two each skip exactly one step (see routing.py's
ROUTING_CONFIG):
  - Official Business:        Employee -> Supervisor -> HR -> AO -> COH
  - Official Time:            Employee -> HR -> AO -> COH        (no Supervisor step)
  - Travel:                   Employee -> Supervisor -> AO -> COH (no HR step)
  - Authorized OT/restday/holiday work: Employee -> Supervisor -> HR -> AO -> COH

Batch filing (Batch 2, Item 7, 2026-10-07): a Supervisor or HR may file
Travel, Official Business or OT/rest-day/holiday work for several
employees and dates as one OfficialRequestBatch; each employee-date is an
OfficialRequest line of it. See batch.py.

Settled 2026-09-28 (CLAUDE.md §7): approving an OT_RESTDAY_HOLIDAY request
does NOT create CTO credit. HR files a separate CTO claim per workday from
the approved request (cto.CTOCreditEntry.ot_request links back here), and
CTO is credited only when that claim is submitted with its required
documents — see cto/claims.py. Nothing in this app touches CTO.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class OfficialRequest(models.Model):
    OFFICIAL_BUSINESS = "OFFICIAL_BUSINESS"
    OFFICIAL_TIME = "OFFICIAL_TIME"
    TRAVEL = "TRAVEL"
    OT_RESTDAY_HOLIDAY = "OT_RESTDAY_HOLIDAY"
    # Owner decisions 2026-11-03: OFFICIAL_BUSINESS is the trip covered by a
    # Governor-approved Travel Order; TRAVEL is a local official errand
    # (nearby, office hours - F59); OFFICIAL_TIME is retired (it only meant a
    # trip without per diem, now the "Expenses authorized" option). The stored
    # codes are kept so old records still read correctly.
    REQUEST_TYPE_CHOICES = [
        (OFFICIAL_BUSINESS, "Official Business (Travel Order)"),
        (OFFICIAL_TIME, "Official Time (no longer used)"),
        (TRAVEL, "Local Official Errand"),
        (OT_RESTDAY_HOLIDAY, "Authorized OT / rest day / holiday work"),
    ]
    ACTIVE_REQUEST_TYPES = [OFFICIAL_BUSINESS, TRAVEL, OT_RESTDAY_HOLIDAY]

    PER_DIEM = "PER_DIEM"
    EXPENSES_OFFICIAL_TIME = "OFFICIAL_TIME"
    EXPENSES_CHOICES = [(PER_DIEM, "Per Diem"), (EXPENSES_OFFICIAL_TIME, "Official Time (no per diem)")]

    SUBMITTED = "SUBMITTED"
    ENDORSED_BY_UNIT_HEAD = "ENDORSED_BY_UNIT_HEAD"  # two-level endorsement (2026-10-27)
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"
    PROCESSED_BY_HR = "PROCESSED_BY_HR"
    RECOMMENDED_BY_AO = "RECOMMENDED_BY_AO"
    # Official Business (Travel Order): the COH only recommends approval; the
    # Governor approves at the Capitol, and HR records that decision
    # (owner decision 2026-11-03).
    FORWARDED_TO_CAPITOL = "FORWARDED_TO_CAPITOL"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    RECORDED = "RECORDED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (ENDORSED_BY_UNIT_HEAD, "Endorsed by Unit Head"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (RECOMMENDED_BY_AO, "Recommended by AO"),
        (FORWARDED_TO_CAPITOL, "Recommended by COH - forwarded to the Capitol"),
        (APPROVED, "Approved"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
        (RECORDED, "Recorded (approved outside BDH)"),
    ]
    TERMINAL_STATUSES = {APPROVED, RECORDED, REJECTED, CANCELLED}

    request_type = models.CharField(max_length=25, choices=REQUEST_TYPE_CHOICES)
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="official_requests"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    time_from = models.TimeField(
        null=True, blank=True, help_text="Mainly for Official Time / a partial-day OT claim."
    )
    time_to = models.TimeField(null=True, blank=True)
    destination = models.CharField(max_length=255, blank=True,
                                   help_text="Required for Official Business (Travel Order) and Local Official Errand.")
    # Travel Order fields (owner decision 2026-11-03).
    expenses_authorized = models.CharField(max_length=20, choices=EXPENSES_CHOICES, blank=True,
                                           help_text="Official Business (Travel Order) only.")
    transportation_allowed = models.CharField(max_length=100, blank=True,
                                              help_text="Official Business (Travel Order) only, e.g. Government Vehicle.")
    purpose = models.TextField()
    hours_requested = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Hours worked — required for Authorized OT/restday/holiday work.",
    )
    is_restday_or_holiday = models.BooleanField(
        default=False, help_text="OT/restday/holiday work only: whether this falls on a rest day or holiday."
    )

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=SUBMITTED)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Set when this request is one employee-date line of a group (batch)
    # request filed by a Supervisor or HR (Batch 2, Item 7). Its status
    # follows the batch's; it is acted on only through the batch.
    batch = models.ForeignKey(
        "OfficialRequestBatch", on_delete=models.PROTECT, null=True, blank=True, related_name="lines",
    )
    line_note = models.CharField(max_length=255, blank=True, help_text="Optional note for this line of a batch.")
    # Owner decision 2026-10-26: the Chief of Hospital's own request has no
    # approver inside BDH (nobody is above the COH). It is RECORDED by HR,
    # like regular leave; the approval itself happens at the PHO/Capitol.
    # Set once, when the request is filed.
    recorded_only = models.BooleanField(
        default=False,
        help_text="Chief of Hospital's own request: HR records it; approval is outside BDH (PHO/Capitol).",
    )

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.get_request_type_display()} — {self.employee} {self.start_date} to {self.end_date} ({self.status})"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date cannot be before the start date.")
        if self.request_type == self.OT_RESTDAY_HOLIDAY and not self.hours_requested:
            raise ValidationError({"hours_requested": "Required for Authorized OT/restday/holiday work."})
        if self.request_type in (self.TRAVEL, self.OFFICIAL_BUSINESS) and not self.destination:
            raise ValidationError({"destination": "Required for this request type."})
        if self.request_type == self.OFFICIAL_BUSINESS and self.batch_id is None:
            errors = {}
            if not self.expenses_authorized:
                errors["expenses_authorized"] = "Choose Per Diem or Official Time."
            if not self.transportation_allowed:
                errors["transportation_allowed"] = "Required for a Travel Order."
            if errors:
                raise ValidationError(errors)

    @property
    def status_label(self):
        """Status wording; an approved Travel Order was approved by the Governor."""
        if self.request_type == self.OFFICIAL_BUSINESS and self.status == self.APPROVED:
            return "Approved by the Governor"
        if self.request_type == self.OFFICIAL_BUSINESS and self.status == self.REJECTED \
                and self.actions.filter(action="record_disapproval").exists():
            return "Disapproved by the Governor"
        return self.get_status_display()

    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES


class OfficialRequestAction(models.Model):
    """Accountability log per routing step — same shape as every other
    request-action model in this system."""

    request = models.ForeignKey(OfficialRequest, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=OfficialRequest.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]

    def __str__(self):
        return f"{self.request} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"


class OfficialRequestBatch(models.Model):
    """
    One group request filed by a Supervisor or HR for several employees and
    dates at once (Batch 2, Item 7; owner decisions 2026-10-07). Each
    employee-date is saved as its own OfficialRequest line (batch=this), so
    the CTO claim, dashboard and the employee's own list work unchanged.

    Approval is for the whole batch. The filer's own step counts as done,
    so the route depends on the type and the filer's role - see
    official_requests/batch.py ROUTES. A regular employee filing their own
    request still uses the normal single-request form and route.
    """

    WORK_OT = "OT"
    WORK_REST_DAY = "REST_DAY"
    WORK_HOLIDAY = "HOLIDAY"
    WORK_KIND_CHOICES = [(WORK_OT, "Authorized overtime"), (WORK_REST_DAY, "Rest-day work"), (WORK_HOLIDAY, "Holiday work")]

    FILER_SUPERVISOR = "SUPERVISOR"
    FILER_HR_PROCESSOR = "HR_PROCESSOR"
    FILER_HR_ADMINISTRATOR = "HR_ADMINISTRATOR"
    FILER_ROLE_CHOICES = [
        (FILER_SUPERVISOR, "Supervisor"),
        (FILER_HR_PROCESSOR, "HR Processor"),
        (FILER_HR_ADMINISTRATOR, "HR Administrator"),
    ]

    request_type = models.CharField(max_length=25, choices=OfficialRequest.REQUEST_TYPE_CHOICES)
    work_kind = models.CharField(max_length=10, choices=WORK_KIND_CHOICES, blank=True,
                                 help_text="OT/rest-day/holiday batches only.")
    purpose = models.TextField(help_text="One shared purpose/reason for the whole batch.")
    destination = models.CharField(max_length=255, blank=True,
                                   help_text="Official Business (Travel Order) and Local Official Errand.")
    expenses_authorized = models.CharField(max_length=20, choices=OfficialRequest.EXPENSES_CHOICES, blank=True)
    transportation_allowed = models.CharField(max_length=100, blank=True)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    filer_role = models.CharField(max_length=20, choices=FILER_ROLE_CHOICES,
                                  help_text="The role the batch was filed under; decides the route.")
    status = models.CharField(max_length=25, choices=OfficialRequest.STATUS_CHOICES, default=OfficialRequest.SUBMITTED)
    filed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-filed_at"]

    def __str__(self):
        return f"Batch #{self.pk} {self.type_label} ({self.status})"

    @property
    def type_label(self):
        if self.request_type == OfficialRequest.OT_RESTDAY_HOLIDAY and self.work_kind:
            return self.get_work_kind_display()
        return self.get_request_type_display()

    def active_lines(self):
        return self.lines.exclude(status=OfficialRequest.CANCELLED).select_related("employee").order_by(
            "employee__surname", "employee__first_name", "start_date", "time_from")

    def is_terminal(self):
        return self.status in OfficialRequest.TERMINAL_STATUSES


class OfficialRequestBatchAction(models.Model):
    """The batch's own history (filing, each approval/return with its
    remark, resubmission). The same entries are also written to every
    line's OfficialRequestAction, so the Audit Log shows them per employee."""

    batch = models.ForeignKey(OfficialRequestBatch, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=OfficialRequest.STATUS_CHOICES)
    notes = models.CharField(max_length=500, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at", "pk"]

    def __str__(self):
        return f"{self.batch} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"


class TravelOrderSettings(models.Model):
    """Names printed on the Travel Order (owner decision 2026-11-03). One
    row; the System Administrator updates it when officials change (for
    example a new Acting Provincial Budget Officer). The defaults come from
    BDH's own Travel Order template."""

    coh_name = models.CharField("Chief of Hospital (as printed)", max_length=150,
                                default="BON KARLO C. LADIA, MD, MHA, MMPHA")
    coh_title = models.CharField(max_length=100, default="Chief of Hospital")
    budget_officer_name = models.CharField(max_length=150, default="MA. ISABEL E. GUINTO")
    budget_officer_title = models.CharField(max_length=100, default="Acting Provincial Budget Officer")
    responsibility_center = models.CharField(max_length=50, default="30-00-05-4421BH")
    governor_name = models.CharField(max_length=150, default="AMY ROA ALVAREZ")
    governor_title = models.CharField(max_length=100, default="Governor")
    office = models.CharField(max_length=150, default="Bataraza District Hospital")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Travel Order settings"
        verbose_name_plural = "Travel Order settings"

    def __str__(self):
        return "Travel Order settings"

    @classmethod
    def get(cls):
        obj = cls.objects.order_by("pk").first()
        return obj or cls.objects.create()
