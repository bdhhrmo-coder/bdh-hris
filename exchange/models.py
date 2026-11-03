"""
Exchange of Duty models — CLAUDE.md §8, based on BDH's approved (Dec 16,
2025) HR Policy Revision Proposal on CTO and Exchange of Duty (the same
policy §7 cites).

Two things are deliberately proxied here, confirmed with the project owner
on 2026-09-27 because CLAUDE.md's text presumes a duty-schedule module that
hasn't been built (and isn't in the Build Order):

  - §8's timing rules ("filed at least 1 week before the next month's
    schedule is approved" for a normal request; "24-72 hours notice" for a
    modification to an already-approved schedule) both key off a schedule-
    approval event this system doesn't record. Since there's no way to
    tell which bucket a given request falls into, exchange/rules.py
    enforces only the one bar both buckets agree on: at least 24 hours'
    notice before the earliest affected date, unless flagged as a
    documented emergency (which §8 allows to be filed same-day, with
    written follow-up within 24 hours — the follow-up itself isn't
    enforced in-system yet, since Phase 9's document uploads aren't built;
    HR records it via the emergency_justification field for now).
  - "Max 3 requests/month" is enforced per employee, per calendar month of
    filing (confirmed 2026-09-27) — counting an employee whether they
    appear as employee_a or employee_b, and only requests that are still
    live (not CONSENT_DECLINED/REJECTED/CANCELLED).

"Mutual written consent between the two employees" (confirmed 2026-09-27)
is a real in-system step: a request sits in PENDING_CONSENT until
employee_b consents, and only then does it enter the normal Employee ->
Supervisor -> HR -> AO -> COH routing chain (§8: "Supervisor endorsement
as part of the full ... chain" — not a substitute for it).

One-for-one by construction: this is a same-day-length shift swap
(employee_a takes employee_b's date_b, and vice versa), never a
grant of extra hours, so "must never generate extra OT, extra CTO, or
extra pay" (§8) is satisfied structurally rather than needing its own
check — there is no field anywhere on this model that could create
additional hours.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class DutyExchangeRequest(models.Model):
    PENDING_CONSENT = "PENDING_CONSENT"
    CONSENT_DECLINED = "CONSENT_DECLINED"
    SUBMITTED = "SUBMITTED"
    ENDORSED_BY_UNIT_HEAD = "ENDORSED_BY_UNIT_HEAD"  # two-level endorsement (2026-10-27)
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"
    PROCESSED_BY_HR = "PROCESSED_BY_HR"
    RECOMMENDED_BY_AO = "RECOMMENDED_BY_AO"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (PENDING_CONSENT, "Pending consent from other employee"),
        (CONSENT_DECLINED, "Consent declined"),
        (SUBMITTED, "Submitted"),
        (ENDORSED_BY_UNIT_HEAD, "Endorsed by Unit Head"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (RECOMMENDED_BY_AO, "Recommended by AO"),
        (APPROVED, "Approved"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]
    DEAD_STATUSES = {CONSENT_DECLINED, REJECTED, CANCELLED}

    employee_a = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="exchange_requests_initiated",
        help_text="The employee who initiates the request.",
    )
    date_a = models.DateField(help_text="Employee A's original assigned duty date.")
    employee_b = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="exchange_requests_received",
        help_text="The employee Employee A proposes to exchange duty with.",
    )
    date_b = models.DateField(help_text="Employee B's original assigned duty date.")

    reason = models.CharField(max_length=255, blank=True)

    is_emergency = models.BooleanField(
        default=False,
        help_text="Documented emergency (§8): allows same-day filing with written follow-up within 24 hours.",
    )
    emergency_justification = models.CharField(
        max_length=255, blank=True, help_text="Required when is_emergency is set — the documentation §8 requires.",
    )

    # Kind of exchange (owner decisions 2026-11-03), decided by the system at
    # filing from the state of that month's duty schedule (exchange/regular.py):
    #   REGULAR      - filed and approved BEFORE the schedule is submitted; the
    #                  employees pick the shifts; at COH approval it is written
    #                  into the supervisor's draft and those days are locked.
    #   MODIFICATION - the schedule is already approved; 24 hours' notice;
    #                  written into the approved schedule at COH approval.
    #   EMERGENCY    - documented emergency, same day allowed; written into a
    #                  submitted/approved schedule only, never into a draft.
    KIND_REGULAR = "REGULAR"
    KIND_MODIFICATION = "MODIFICATION"
    KIND_EMERGENCY = "EMERGENCY"
    KIND_CHOICES = [
        (KIND_REGULAR, "Regular (before the schedule is submitted)"),
        (KIND_MODIFICATION, "Change to an approved schedule"),
        (KIND_EMERGENCY, "Emergency"),
    ]
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default=KIND_MODIFICATION)
    # The shift each one gives up: B works shift_a on date_a, A works shift_b
    # on date_b. Picked by the employees for a regular exchange (no schedule
    # yet); copied from the schedule for the other kinds.
    shift_a = models.ForeignKey("schedules.ShiftCode", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+", verbose_name="Shift on your date")
    shift_b = models.ForeignKey("schedules.ShiftCode", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+", verbose_name="Shift on their date")

    # Still waiting somewhere in the chain (consent included).
    LIVE_STATUSES = {PENDING_CONSENT, SUBMITTED, ENDORSED_BY_UNIT_HEAD, ENDORSED_BY_SUPERVISOR,
                     PROCESSED_BY_HR, RECOMMENDED_BY_AO}

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=PENDING_CONSENT)
    filed_at = models.DateTimeField(auto_now_add=True)
    consented_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-filed_at"]

    def __str__(self):
        return (
            f"{self.employee_a} <-> {self.employee_b}: "
            f"{self.date_a} for {self.date_b} ({self.status})"
        )

    def clean(self):
        if self.employee_a_id and self.employee_b_id and self.employee_a_id == self.employee_b_id:
            raise ValidationError("Employee A and Employee B must be two different employees.")
        if self.date_a and self.date_b and self.date_a == self.date_b:
            raise ValidationError("An exchange requires two different duty dates to swap.")

    @property
    def shifts_label(self):
        if not (self.shift_a_id or self.shift_b_id):
            return ""
        return f"{self.shift_a.code if self.shift_a_id else '?'} / {self.shift_b.code if self.shift_b_id else '?'}"

    @property
    def earliest_affected_date(self):
        return min(self.date_a, self.date_b)


class DutyExchangeRequestAction(models.Model):
    """Accountability log per step — same shape as LeaveApplicationAction /
    CTOUsageApplicationAction, extended with the consent step."""

    request = models.ForeignKey(DutyExchangeRequest, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=DutyExchangeRequest.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]

    def __str__(self):
        return f"{self.request} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"
