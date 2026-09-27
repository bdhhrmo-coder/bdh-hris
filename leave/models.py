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
    A leave application. Status choices are shared, but which ones are
    reachable depends entirely on leave_type.requires_full_routing —
    enforced in leave/views.py, not here. This is deliberate: CLAUDE.md is
    explicit that Regular Leave must never be routed through the internal
    approval engine, so the two paths are kept structurally distinct in the
    view/permission layer rather than relying on this model alone.

    Data-entry-only path (everything except COSP Leave):
        SUBMITTED -> RECORDED (by HR)  or  REJECTED

    Full routing path (COSP Leave only):
        SUBMITTED -> ENDORSED_BY_SUPERVISOR -> PROCESSED_BY_HR
                   -> RECOMMENDED_BY_AO -> APPROVED
        (REJECTED or RETURNED possible at any stage)
    """

    SUBMITTED = "SUBMITTED"
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"
    PROCESSED_BY_HR = "PROCESSED_BY_HR"
    RECOMMENDED_BY_AO = "RECOMMENDED_BY_AO"
    APPROVED = "APPROVED"
    RECORDED = "RECORDED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (RECOMMENDED_BY_AO, "Recommended by AO"),
        (APPROVED, "Approved"),
        (RECORDED, "Recorded"),
        (RETURNED, "Returned for correction"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]

    TERMINAL_STATUSES = {APPROVED, RECORDED, REJECTED, CANCELLED}

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="leave_applications"
    )
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="applications")
    start_date = models.DateField()
    end_date = models.DateField()
    number_of_days = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Working days covered. Entered directly (no holiday calendar yet), same as on the paper form.",
    )
    justification = models.TextField(
        blank=True, help_text="Required for Emergency Leave; optional otherwise."
    )
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=SUBMITTED)

    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.employee} — {self.leave_type.code} {self.start_date} to {self.end_date} ({self.status})"

    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES


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

    def __str__(self):
        return f"{self.application} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"
