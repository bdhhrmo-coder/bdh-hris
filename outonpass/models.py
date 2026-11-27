"""
Personal Out-on-Pass (CLAUDE.md section 13B; BDH-ADM-AO-01 as revised by the
pending DICR, Rev 1; slip BDH-ADM-AO-01F16).

Stage 1 (2026-11-14): the records and the rules checker only (rules.py).
Filing screens, routing, Tap Out / Tap In and the slip come in later stages.

Time is kept in whole MINUTES everywhere (no rounding, seconds ignored);
hours are only a display unit.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CospContractPeriod(models.Model):
    """A COSP contract period, entered by HR. The COSP allowance is 1 hour
    per FULL month of contract inside the policy year, up to 12 hours; a
    renewal in the same year adds its months and keeps the balance."""

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="cosp_contract_periods")
    start_date = models.DateField()
    end_date = models.DateField()
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entered_at = models.DateTimeField(auto_now_add=True)
    # A wrong entry is voided with a reason, never deleted or edited (like CTO opening lines).
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["employee", "start_date"]

    def __str__(self):
        return f"{self.employee}: {self.start_date} to {self.end_date}"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("The contract end date is before its start date.")


class PassOpeningUsage(models.Model):
    """Minutes already used in a policy year before the HRIS took over
    (Jan 1-10, 2027, from the Google Sheets tracker). One line per employee
    per year, with a reason; counted as used."""

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT,
                                 related_name="pass_opening_usage")
    year = models.PositiveSmallIntegerField()
    minutes_used = models.PositiveIntegerField()
    passes_used_in_month = models.PositiveSmallIntegerField(
        default=0, help_text="Passes already taken in January (counts toward that month's limit).")
    reason = models.CharField(max_length=255, help_text="e.g. 'Google Sheets tracker, Jan 1-10, 2027'.")
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entered_at = models.DateTimeField(auto_now_add=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "year"], condition=models.Q(voided_at__isnull=True),
                                               name="one_opening_usage_per_year")]

    def __str__(self):
        return f"{self.employee}: {self.minutes_used} min used before the HRIS ({self.year})"


class OutOnPass(models.Model):
    # Request types (owner decision 2026-11-14, Option A): ordinary passes go
    # Supervisor -> AO or COH; the three special types go Supervisor -> COH alone.
    ORDINARY = "ORDINARY"
    THIRD = "THIRD"
    EXCEPTION = "EXCEPTION"
    EMERGENCY = "EMERGENCY"
    KIND_CHOICES = [
        (ORDINARY, "Personal pass"),
        (THIRD, "Third pass this month"),
        (EXCEPTION, "Exception (beyond balance or 2 hours)"),
        (EMERGENCY, "Documented emergency pass"),
    ]
    SPECIAL_KINDS = (THIRD, EXCEPTION, EMERGENCY)

    SUBMITTED = "SUBMITTED"
    ENDORSED = "ENDORSED"
    APPROVED = "APPROVED"
    RECORDED = "RECORDED"
    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted (for supervisor endorsement)"),
        (ENDORSED, "Endorsed by supervisor"),
        (APPROVED, "Approved"),
        (RECORDED, "Recorded by HR (Chief of Hospital's own pass)"),
        (COMPLETED, "Completed (back in)"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]
    # Statuses that hold a slot: they count toward the daily/monthly limits,
    # the unit limit and (as planned minutes) the balance.
    LIVE_STATUSES = (SUBMITTED, ENDORSED, APPROVED, RECORDED)
    # Cleared to leave: the employee may Tap Out (stage 3).
    CLEARED_STATUSES = (APPROVED, RECORDED)
    PENDING_STATUSES = (SUBMITTED, ENDORSED)

    # Part I reasons, as on slip BDH-ADM-AO-01F16.
    REASON_MEDICAL = "MEDICAL"
    REASON_LEGAL = "LEGAL"
    REASON_GOVT_BANK = "GOVT_BANK"
    REASON_FAMILY = "FAMILY"
    REASON_ERRAND = "ERRAND"
    REASON_OTHER = "OTHER"
    REASON_CHOICES = [
        (REASON_MEDICAL, "Medical appointment"),
        (REASON_LEGAL, "Legal matter"),
        (REASON_GOVT_BANK, "Government or bank transaction"),
        (REASON_FAMILY, "Family emergency"),
        (REASON_ERRAND, "Personal errand"),
        (REASON_OTHER, "Other"),
    ]

    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="out_on_passes")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=ORDINARY)
    pass_date = models.DateField()
    time_out = models.TimeField(help_text="Planned time out.")
    time_in = models.TimeField(help_text="Expected time back. Earlier than the time out = the next day.")
    reason_type = models.CharField("Reason", max_length=10, choices=REASON_CHOICES)
    reason = models.TextField("Details")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=SUBMITTED)
    recorded_only = models.BooleanField(
        default=False, help_text="The Chief of Hospital's own pass: recorded by HR, no approval step "
                                 "(owner decision 2026-11-14, Option B).")

    # Supervisor confirmations at endorsement (CLAUDE.md 13B): no surge or
    # Code activation, coverage, and the shift-hour rule when the employee
    # has no approved duty schedule for the day.
    covering_staff = models.CharField(max_length=255, blank=True)
    confirmed_no_surge = models.BooleanField(default=False)
    confirmed_coverage = models.BooleanField(default=False)
    confirmed_shift_hours = models.BooleanField(default=False)

    # Actual gate times (stage 3: Tap Out / Tap In on a hospital computer).
    tapped_out_at = models.DateTimeField(null=True, blank=True)
    tapped_in_at = models.DateTimeField(null=True, blank=True)
    minutes_charged = models.PositiveIntegerField(null=True, blank=True)
    excess_minutes = models.PositiveIntegerField(null=True, blank=True,
                                                 help_text="Minutes over 2 hours, beyond the 5-minute grace.")
    over_balance_minutes = models.PositiveIntegerField(
        null=True, blank=True, help_text="Charged minutes the balance did not cover when the pass was completed.")
    completed_at = models.DateTimeField(null=True, blank=True)

    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    filed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pass_date", "-time_out"]
        verbose_name = "out-on-pass"
        verbose_name_plural = "out-on-passes"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.employee} on {self.pass_date} {self.time_out:%H:%M}"

    @property
    def pass_number(self):
        """Shown to the guard for the cross-check log."""
        return f"OOP-{self.pass_date:%Y}-{self.pk:05d}" if self.pk else ""

    @property
    def is_special(self):
        return self.kind in self.SPECIAL_KINDS

    def may_view_document(self, acting_employee):
        from .routing import may_view_proof
        return may_view_proof(acting_employee, self)

    @property
    def planned_minutes(self):
        from .rules import planned_minutes
        return planned_minutes(self.time_out, self.time_in)


class OutOnPassAction(models.Model):
    """Accountability log per step, same shape as the other request logs."""

    out_on_pass = models.ForeignKey(OutOnPass, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=10, choices=OutOnPass.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # Part A (2026-11-27): in what capacity the actor acted (display only;
    # accounts/processors.py). Blank on rows saved before this change.
    acting_capacity = models.CharField(max_length=10, blank=True, default="")
    acting_for = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]


class MissedTap(models.Model):
    """A forgotten Tap Out or Tap In (CLAUDE.md 13B, DICR Rev 1): the employee
    gives the time; the supervisor confirms it, the guard on duty verifies
    it (and may correct it), and HRMO reviews it before hours are charged."""

    OUT = "OUT"
    IN = "IN"
    WHICH_CHOICES = [(OUT, "Tap Out"), (IN, "Tap In")]

    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    VERIFIED = "VERIFIED"
    REVIEWED = "REVIEWED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted (for supervisor confirmation)"),
        (CONFIRMED, "Confirmed by supervisor (for the guard on duty)"),
        (VERIFIED, "Verified by guard (for HRMO review)"),
        (REVIEWED, "Reviewed by HRMO (time recorded)"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
    ]
    PENDING_STATUSES = (SUBMITTED, CONFIRMED, VERIFIED)

    out_on_pass = models.ForeignKey(OutOnPass, on_delete=models.CASCADE, related_name="missed_taps")
    which = models.CharField(max_length=3, choices=WHICH_CHOICES)
    claimed_at = models.DateTimeField("Actual time", help_text="When you actually went out / came back.")
    reason = models.CharField(max_length=255, help_text="Why the tap was missed.")
    verified_at = models.DateTimeField(null=True, blank=True,
                                       help_text="The time the guard on duty verified (may differ from the claim).")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=SUBMITTED)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    filed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-filed_at"]

    def __str__(self):
        return f"Missed {self.get_which_display()} on {self.out_on_pass.pass_number}"

    @property
    def employee(self):
        return self.out_on_pass.employee

    @property
    def employee_id(self):
        return self.out_on_pass.employee_id

    @property
    def claimed_input(self):
        """The claimed time for a datetime-local input, in local time."""
        from django.utils import timezone
        return f"{timezone.localtime(self.claimed_at):%Y-%m-%dT%H:%M}"

    @property
    def time_to_record(self):
        return self.verified_at or self.claimed_at


class MissedTapAction(models.Model):
    missed_tap = models.ForeignKey(MissedTap, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=10, choices=MissedTap.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # Part A (2026-11-27): in what capacity the actor acted (display only;
    # accounts/processors.py). Blank on rows saved before this change.
    acting_capacity = models.CharField(max_length=10, blank=True, default="")
    acting_for = models.ForeignKey("employees.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]
