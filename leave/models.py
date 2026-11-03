from django.conf import settings
from django.db import models


class LeaveType(models.Model):
    """
    A kind of leave an employee can apply for (CLAUDE.md §6.1). Kept as a
    configurable table rather than hardcoded choices, the same way CTO
    multipliers are kept configurable — so HR can adjust caps without a
    code change if policy is revised.

    balance_tracking controls how (or whether) a numeric balance is
    enforced:
      - ACCRUED: earns monthly_accrual_days per month of service, balance
        is cumulative (VL, SL).
      - ANNUAL_CAP: a fixed number of days per year (annual_fixed_days);
        is_cumulative decides whether unused days carry into the next year
        or reset (Wellness resets; COSP Leave carries over).
      - UNTRACKED: recorded for monitoring, but this system does not
        enforce a numeric cap. Used for the CSC special leave types
        (Maternity, Paternity, Solo Parent, VAWC, etc.) and Emergency
        Leave, whose exact statutory entitlements/eligibility conditions
        are not encoded here — see CLAUDE.md working rules: those numbers
        were not confirmed for this build and should not be guessed at.

    requires_full_routing is True only for COSP Leave — every other type
    is recorded by HR without going through Supervisor/AO/COH, per the
    confirmed Phase 4 decision (all CSC-listed types are data-entry only,
    and Wellness/Emergency for COSP is an HR-only balance check).
    """

    APPLICABLE_REGULAR = "REGULAR"
    APPLICABLE_COSP = "COSP"
    APPLICABLE_BOTH = "BOTH"
    APPLICABLE_CHOICES = [
        (APPLICABLE_REGULAR, "Regular employees only"),
        (APPLICABLE_COSP, "COSP employees only"),
        (APPLICABLE_BOTH, "Both"),
    ]

    TRACKING_ACCRUED = "ACCRUED"
    TRACKING_ANNUAL_CAP = "ANNUAL_CAP"
    TRACKING_UNTRACKED = "UNTRACKED"
    TRACKING_CHOICES = [
        (TRACKING_ACCRUED, "Monthly accrual"),
        (TRACKING_ANNUAL_CAP, "Fixed annual allotment"),
        (TRACKING_UNTRACKED, "Recorded, no system-enforced cap"),
    ]

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    applicable_to = models.CharField(max_length=10, choices=APPLICABLE_CHOICES, default=APPLICABLE_BOTH)

    requires_full_routing = models.BooleanField(
        default=False,
        help_text="True only for COSP Leave. Everything else is recorded by HR "
        "without Supervisor/AO/COH routing.",
    )
    requires_justification = models.BooleanField(
        default=False, help_text="True for Emergency Leave — must state why it's justified."
    )

    balance_tracking = models.CharField(max_length=15, choices=TRACKING_CHOICES, default=TRACKING_UNTRACKED)
    monthly_accrual_days = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    annual_fixed_days = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    is_cumulative = models.BooleanField(
        default=True, help_text="Only meaningful for ANNUAL_CAP: do unused days carry into next year?"
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LeaveCreditTransaction(models.Model):
    """
    Append-only ledger entry for a leave balance. Balance for an
    (employee, leave_type) is the sum of `days` across their transactions —
    positive for a grant/earned credit, negative for usage or forfeiture.
    Kept as a ledger (not a single mutable balance field) so every change
    is traceable, per the project's "no black box" / rebuildability rule.
    """

    EARNED = "EARNED"
    USED = "USED"
    ADJUSTMENT = "ADJUSTMENT"
    FORFEITED = "FORFEITED"
    TRANSACTION_TYPE_CHOICES = [
        (EARNED, "Earned/granted"),
        (USED, "Used"),
        (ADJUSTMENT, "Manual adjustment"),
        (FORFEITED, "Forfeited"),
    ]

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_credit_transactions"
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="+")
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPE_CHOICES)
    days = models.DecimalField(
        max_digits=6, decimal_places=2, help_text="Positive for a credit, negative for a deduction."
    )
    transaction_date = models.DateField()
    leave_application = models.ForeignKey(
        "LeaveApplication", on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries"
    )
    attendance_record = models.ForeignKey(
        "attendance.AttendanceRecord", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="undertime_transactions",
        help_text="Set only for the auto-deduction CLAUDE.md §9 requires ('Undertime auto-deducts from "
        "leave credits') — confirmed 2026-09-27 that undertime charges against VL specifically.",
    )
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-id"]

    def __str__(self):
        return f"{self.employee} — {self.leave_type.code} {self.days:+} ({self.transaction_date})"


class LeaveApplication(models.Model):
    """
    A leave application. Two chains, fixed per application by `route`
    (dry-run items 2 and 3, owner decisions 2026-11-03):

    COSP route (every leave of a COSP employee: COSP Leave, Wellness,
    Emergency) - approved inside BDH, prints on BDH-ADM-HR-01F04-A:
        SUBMITTED [-> ENDORSED_BY_UNIT_HEAD] -> ENDORSED_BY_SUPERVISOR
          -> PROCESSED_BY_HR -> RECOMMENDED_BY_AO -> APPROVED (credits deducted)

    Regular route (every leave of a regular employee) - reviewed inside
    BDH, approved at the Capitol, prints on CSC Form No. 6 (no stamps):
        SUBMITTED [-> ENDORSED_BY_UNIT_HEAD] -> ENDORSED_BY_SUPERVISOR
          -> PROCESSED_BY_HR ("recorded and verified", credits deducted)
          -> RECOMMENDED_BY_AO ("reviewed and endorsed")
          -> FOR_CAPITOL_APPROVAL (COH recommends approval)
        HR then records a Capitol return or rejection, if any. HR cannot
        reject; the other steps may. Credits come back automatically on
        any return or rejection after HR deducted them.

    RECORDED is the older regular-leave path (HR only), kept for records
    filed before 2026-11-03. Returns and rejections need a remark.
    """

    SUBMITTED = "SUBMITTED"
    ENDORSED_BY_UNIT_HEAD = "ENDORSED_BY_UNIT_HEAD"  # two-level endorsement (2026-10-27)
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"
    PROCESSED_BY_HR = "PROCESSED_BY_HR"
    RECOMMENDED_BY_AO = "RECOMMENDED_BY_AO"
    APPROVED = "APPROVED"
    RECORDED = "RECORDED"
    # Regular leave (dry-run item 2, owner 2026-11-03): the COH recommends
    # approval and the application waits for the Capitol. The HRIS never
    # marks a regular leave "approved".
    FOR_CAPITOL_APPROVAL = "FOR_CAPITOL_APPROVAL"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (ENDORSED_BY_UNIT_HEAD, "Endorsed by Unit Head"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (RECOMMENDED_BY_AO, "Recommended by AO"),
        (APPROVED, "Approved"),
        (RECORDED, "Recorded"),
        (FOR_CAPITOL_APPROVAL, "For Capitol approval"),
        (RETURNED, "Returned for correction"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]

    TERMINAL_STATUSES = {APPROVED, RECORDED, REJECTED, CANCELLED}

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_applications"
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="applications")
    # Blank only for "Other purpose" (monetization / terminal leave), where the
    # CSC form has no inclusive dates - see LeaveApplicationForm.clean().
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    number_of_days = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Working days covered. Entered directly (no holiday calendar yet), same as on the paper form.",
    )
    justification = models.TextField(
        blank=True, help_text="Required for Emergency Leave; optional otherwise."
    )
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=SUBMITTED)

    # Which chain this application follows, fixed when it is filed
    # (dry-run items 2 and 3, owner 2026-11-03): every leave of a COSP
    # employee (COSP Leave, Wellness, Emergency) is approved inside BDH;
    # every leave of a regular employee is reviewed inside BDH and approved
    # at the Capitol. route_for() decides.
    ROUTE_COSP = "COSP"
    ROUTE_REGULAR = "REGULAR"
    ROUTE_CHOICES = [(ROUTE_COSP, "Approved inside BDH (COSP)"),
                     (ROUTE_REGULAR, "Reviewed inside BDH, approved at the Capitol")]
    route = models.CharField(max_length=10, choices=ROUTE_CHOICES, default=ROUTE_REGULAR)

    # --- CSC Form No. 6 (Revised 2020), 6.B Details of Leave and 6.D (Batch 8) ---
    # Which fields apply to which leave type: DETAIL_FIELDS_BY_TYPE below.
    LOCATION_PH = "PH"
    LOCATION_ABROAD = "ABROAD"
    LOCATION_CHOICES = [(LOCATION_PH, "Within the Philippines"), (LOCATION_ABROAD, "Abroad (Specify)")]
    SICK_HOSPITAL = "HOSPITAL"
    SICK_OUTPATIENT = "OUTPATIENT"
    SICK_CHOICES = [(SICK_HOSPITAL, "In Hospital (Specify Illness)"), (SICK_OUTPATIENT, "Out Patient (Specify Illness)")]
    STUDY_MASTERS = "MASTERS"
    STUDY_BAR = "BAR_BOARD"
    STUDY_CHOICES = [(STUDY_MASTERS, "Completion of Master's Degree"), (STUDY_BAR, "BAR/Board Examination Review")]
    PURPOSE_MONETIZATION = "MONETIZATION"
    PURPOSE_TERMINAL = "TERMINAL"
    PURPOSE_CHOICES = [(PURPOSE_MONETIZATION, "Monetization of Leave Credits"), (PURPOSE_TERMINAL, "Terminal Leave")]
    COMMUTATION_NOT_REQUESTED = "NOT_REQUESTED"
    COMMUTATION_REQUESTED = "REQUESTED"
    COMMUTATION_CHOICES = [(COMMUTATION_NOT_REQUESTED, "Not Requested"), (COMMUTATION_REQUESTED, "Requested")]
    # Wellness Leave purposes, as listed in CSC MC No. 1, s. 2026 (CSC Res. No. 2501292).
    WELLNESS_MENTAL = "MENTAL_HEALTH"
    WELLNESS_PHYSICAL = "PHYSICAL"
    WELLNESS_BREAK = "GENERAL_BREAK"
    WELLNESS_CHOICES = [(WELLNESS_MENTAL, "Mental Health Care"), (WELLNESS_PHYSICAL, "Physical Wellness Activities"),
                        (WELLNESS_BREAK, "General Break from Work")]

    location = models.CharField("Location", max_length=10, choices=LOCATION_CHOICES, blank=True)
    location_detail = models.CharField("Place", max_length=200, blank=True)
    sick_leave_type = models.CharField("Sick leave", max_length=12, choices=SICK_CHOICES, blank=True)
    illness = models.CharField("Illness", max_length=200, blank=True)
    study_purpose = models.CharField("Study leave purpose", max_length=12, choices=STUDY_CHOICES, blank=True)
    other_purpose = models.CharField("Other purpose", max_length=15, choices=PURPOSE_CHOICES, blank=True)
    commutation = models.CharField("6.D Commutation", max_length=15, choices=COMMUTATION_CHOICES,
                                   default=COMMUTATION_NOT_REQUESTED)
    wellness_reason = models.CharField("Wellness Leave reason", max_length=15, choices=WELLNESS_CHOICES, blank=True)

    # leave_type.code -> the 6.B fields that type uses (all others are cleared on save).
    DETAIL_FIELDS_BY_TYPE = {
        "VL": ["location", "location_detail", "other_purpose"],
        "SPL": ["location", "location_detail"],
        "SL": ["sick_leave_type", "illness", "other_purpose"],
        "SPECIAL_WOMEN": ["illness"],
        "STUDY": ["study_purpose"],
        "WELLNESS": ["wellness_reason"],
    }
    ALL_DETAIL_FIELDS = ["location", "location_detail", "sick_leave_type", "illness", "study_purpose",
                         "other_purpose", "wellness_reason"]

    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.employee} — {self.leave_type.code} {self.dates_label} ({self.status})"

    def save(self, *args, **kwargs):
        # The route is fixed whenever the application is (re)submitted, so a
        # test or import that creates one directly gets the right chain too.
        if self.status == self.SUBMITTED and self.employee_id and self.leave_type_id:
            self.route = self.route_for(self.employee, self.leave_type)
            if kwargs.get("update_fields") is not None and "route" not in kwargs["update_fields"]:
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["route"]
        super().save(*args, **kwargs)

    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES

    @staticmethod
    def route_for(employee, leave_type):
        from employees.models import Employee

        if leave_type is not None and leave_type.requires_full_routing:
            return LeaveApplication.ROUTE_COSP
        if employee is not None and employee.employment_status == Employee.EMPLOYMENT_STATUS_COSP:
            return LeaveApplication.ROUTE_COSP
        return LeaveApplication.ROUTE_REGULAR

    @property
    def coh_own(self):
        """The COH's own regular leave (HR records it; see permissions.coh_filed_regular)."""
        from .permissions import coh_filed_regular

        return coh_filed_regular(self)

    @property
    def is_cosp_route(self):
        return self.route == self.ROUTE_COSP

    @classmethod
    def counted_q(cls):
        """Applications whose days are taken (credits deducted): approved
        (COSP), recorded (older regular leave), or regular leave from the
        moment HR recorded and verified it until the Capitol decides."""
        from django.db.models import Q

        return Q(status__in=[cls.APPROVED, cls.RECORDED, cls.FOR_CAPITOL_APPROVAL]) | Q(
            route=cls.ROUTE_REGULAR, status__in=[cls.PROCESSED_BY_HR, cls.RECOMMENDED_BY_AO])

    # Regular-leave step names (owner 2026-11-03); COSP keeps the stored labels.
    _REGULAR_LABELS = {
        "PROCESSED_BY_HR": "Recorded and verified by HR",
        "RECOMMENDED_BY_AO": "Reviewed and endorsed by AO",
    }

    @property
    def status_label(self):
        if self.route == self.ROUTE_REGULAR and self.status in self._REGULAR_LABELS:
            return self._REGULAR_LABELS[self.status]
        if self.status == self.ENDORSED_BY_SUPERVISOR and self.pk \
                and self.actions.filter(action="endorse_unit").exists():
            return "Recommended by Section Head"
        return self.get_status_display()

    @property
    def dates_label(self):
        if self.start_date and self.end_date:
            if self.start_date == self.end_date:
                return f"{self.start_date:%b %d, %Y}"
            return f"{self.start_date:%b %d, %Y} to {self.end_date:%b %d, %Y}"
        return "Not applicable"

    def details_6b(self):
        """[(label, value)] of the 6.B details that were entered, for screens."""
        rows = []
        if self.location:
            place = f": {self.location_detail}" if self.location_detail else ""
            rows.append(("Vacation/Special Privilege Leave",
                         ("Within the Philippines" if self.location == self.LOCATION_PH else "Abroad") + place))
        if self.sick_leave_type:
            rows.append(("Sick Leave", f"{self.get_sick_leave_type_display().split(' (')[0]}: {self.illness}"))
        elif self.illness:
            rows.append(("Special Leave Benefits for Women", f"Illness: {self.illness}"))
        if self.study_purpose:
            rows.append(("Study Leave", self.get_study_purpose_display()))
        if self.other_purpose:
            rows.append(("Other purpose", self.get_other_purpose_display()))
        if self.wellness_reason:
            rows.append(("Wellness Leave reason", self.get_wellness_reason_display()))
        return rows

    def queue_details(self):
        """Short details for the approver lists (dry-run item 9, 2026-11-03).
        Illness is left out: it is medical information and stays on the
        application's own page."""
        return "; ".join(f"{label}: {value}" for label, value in self.details_6b()
                         if label not in ("Sick Leave", "Special Leave Benefits for Women"))

    def is_editable_by_filer(self):
        """The filer may correct the application before anyone acts on it,
        or after it is returned (Batch 8)."""
        return self.status in (self.SUBMITTED, self.RETURNED)


class LeaveApplicationAction(models.Model):
    """
    One row per routing step taken on a LeaveApplication (submit, endorse,
    process, recommend, approve, reject, return, record, cancel) — the
    accountability record for who did what and when, separate from the
    Employee-record audit trail in employees.EmployeeEditHistory.
    """

    application = models.ForeignKey(
        LeaveApplication, on_delete=models.CASCADE, related_name="actions"
    )
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=LeaveApplication.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]

    LABELS = {
        "submit": "Filed", "resubmit": "Resubmitted", "edit": "Edited",
        "endorse_unit": "Endorsed by Unit Head", "endorse": "Endorsed",
        "process": "Processed by HR", "record": "Recorded and verified by HR (credits deducted)",
        "recommend": "Recommended by AO", "review": "Reviewed and endorsed by AO",
        "approve": "Approved by COH", "recommend_capitol": "Recommended for approval by COH (for Capitol approval)",
        "return": "Returned", "reject": "Rejected",
        "capitol_return": "Returned by the Capitol (recorded by HR)",
        "capitol_reject": "Rejected by the Capitol (recorded by HR)",
    }

    @property
    def label(self):
        return self.LABELS.get(self.action, self.action.replace("_", " ").capitalize())

    def __str__(self):
        return f"{self.application} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"
