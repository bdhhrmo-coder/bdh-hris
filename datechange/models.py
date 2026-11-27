"""
Date change of an approved leave or CTO application (Part 2, owner
decisions 2026-11-19).

The employee asks to move their own approved request to new dates, with a
written justification. The change follows the same route as the original
request and takes effect only when the last internal step acts:

    SUBMITTED [-> ENDORSED_BY_UNIT_HEAD] -> ENDORSED_BY_SUPERVISOR
      -> PROCESSED_BY_HR -> RECOMMENDED_BY_AO -> APPLIED
    (the COH approves for COSP leave and CTO; for regular leave the COH
     recommends, as on the original - the Capitol is outside the HRIS)

While it is pending the original dates stay in force; a return or a
rejection leaves them as they are. When it is applied, the same leave or
CTO record takes the new dates: the number of days, the credits taken and
the CTO credit held are untouched, so the balance never changes.
"""

from django.conf import settings
from django.db import models


class DateChangeRequest(models.Model):
    KIND_LEAVE = "LEAVE"
    KIND_CTO = "CTO"
    KIND_CHOICES = [(KIND_LEAVE, "Leave"), (KIND_CTO, "CTO")]

    SUBMITTED = "SUBMITTED"
    ENDORSED_BY_UNIT_HEAD = "ENDORSED_BY_UNIT_HEAD"
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"
    PROCESSED_BY_HR = "PROCESSED_BY_HR"
    RECOMMENDED_BY_AO = "RECOMMENDED_BY_AO"
    APPLIED = "APPLIED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (ENDORSED_BY_UNIT_HEAD, "Endorsed by Unit Head"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (RECOMMENDED_BY_AO, "Recommended by AO"),
        (APPLIED, "Approved - new dates in force"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
    ]
    OPEN_STATUSES = (SUBMITTED, ENDORSED_BY_UNIT_HEAD, ENDORSED_BY_SUPERVISOR, PROCESSED_BY_HR, RECOMMENDED_BY_AO)

    kind = models.CharField(max_length=5, choices=KIND_CHOICES)
    leave_application = models.ForeignKey("leave.LeaveApplication", null=True, blank=True,
                                          on_delete=models.PROTECT, related_name="date_changes")
    cto_application = models.ForeignKey("cto.CTOUsageApplication", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="date_changes")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="date_changes")
    old_start_date = models.DateField()
    old_end_date = models.DateField()
    new_start_date = models.DateField()
    new_end_date = models.DateField()
    number_of_days = models.DecimalField(max_digits=5, decimal_places=2,
                                         help_text="Copied from the original; a date change never changes it.")
    justification = models.TextField()
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=SUBMITTED)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.CheckConstraint(
                name="datechange_one_target",
                condition=(models.Q(leave_application__isnull=False, cto_application__isnull=True)
                           | models.Q(leave_application__isnull=True, cto_application__isnull=False)),
            ),
        ]

    def __str__(self):
        return f"Date change #{self.pk} - {self.employee}: {self.old_dates_label} to {self.new_dates_label}"

    @property
    def target(self):
        return self.leave_application if self.kind == self.KIND_LEAVE else self.cto_application

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def regular_leave(self):
        """Regular employees' leave: the COH recommends; the Capitol approves outside the HRIS."""
        return self.kind == self.KIND_LEAVE and not self.leave_application.is_cosp_route

    @property
    def what(self):
        return self.leave_application.leave_type.name if self.kind == self.KIND_LEAVE else "CTO"

    @staticmethod
    def _label(start, end):
        return f"{start:%b %d, %Y}" if start == end else f"{start:%b %d, %Y} to {end:%b %d, %Y}"

    @property
    def old_dates_label(self):
        return self._label(self.old_start_date, self.old_end_date)

    @property
    def new_dates_label(self):
        return self._label(self.new_start_date, self.new_end_date)

    @property
    def status_label(self):
        if self.status == self.APPLIED and self.regular_leave:
            return "Recommended by COH - new dates in force"
        return self.get_status_display()


class DateChangeAction(models.Model):
    """Who did what, when, on a date change request (shown in the request
    history and in the Audit Log)."""

    LABELS = {
        "submit": "Date change requested", "endorse_unit": "Endorsed by Unit Head", "endorse": "Endorsed",
        "process": "Processed by HR", "recommend": "Recommended by AO",
        "approve": "Approved by COH (new dates in force)",
        "recommend_capitol": "Recommended by COH (new dates in force)",
        "return": "Returned", "reject": "Rejected",
    }

    request = models.ForeignKey(DateChangeRequest, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=DateChangeRequest.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # Part A (2026-11-27): in what capacity the actor acted (display only;
    # accounts/processors.py). Blank on rows saved before this change.
    acting_capacity = models.CharField(max_length=10, blank=True, default="")
    acting_for = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at", "pk"]

    @property
    def label(self):
        return self.LABELS.get(self.action, self.action.replace("_", " ").capitalize())

    def __str__(self):
        return f"Date change #{self.request_id}: {self.old_new}"

    @property
    def old_new(self):
        r = self.request
        return f"{r.what} {r.old_dates_label} -> {r.new_dates_label}"
