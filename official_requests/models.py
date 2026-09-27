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

Deliberately NOT wired here (per the Build Order's "no special business
rules yet"): an approved OT_RESTDAY_HOLIDAY request does not yet create a
cto.CTOCreditEntry automatically, even though cto/models.py's module
docstring flags this as the natural next step once this phase existed.
HR still records CTO credit by hand (cto.credit_entry_create) for now;
wiring approval here to auto-credit CTO is real business logic beyond
this phase's stated scope, and should be its own confirmed decision,
not folded in silently.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class OfficialRequest(models.Model):
    OFFICIAL_BUSINESS = "OFFICIAL_BUSINESS"
    OFFICIAL_TIME = "OFFICIAL_TIME"
    TRAVEL = "TRAVEL"
    OT_RESTDAY_HOLIDAY = "OT_RESTDAY_HOLIDAY"
    REQUEST_TYPE_CHOICES = [
        (OFFICIAL_BUSINESS, "Official Business"),
        (OFFICIAL_TIME, "Official Time"),
        (TRAVEL, "Travel"),
        (OT_RESTDAY_HOLIDAY, "Authorized OT / rest day / holiday work"),
    ]

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
    destination = models.CharField(max_length=255, blank=True, help_text="Mainly for Travel.")
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

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.get_request_type_display()} — {self.employee} {self.start_date} to {self.end_date} ({self.status})"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date cannot be before the start date.")
        if self.request_type == self.OT_RESTDAY_HOLIDAY and not self.hours_requested:
            raise ValidationError({"hours_requested": "Required for Authorized OT/restday/holiday work."})
        if self.request_type == self.TRAVEL and not self.destination:
            raise ValidationError({"destination": "Required for a Travel request."})

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
