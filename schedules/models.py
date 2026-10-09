"""
Monthly duty schedules - Form BDH-ADM-AO-01F50, Personnel Work Schedule
(Batch 5, owner decisions 2026-10-09).

- One schedule per section OR unit per month. Rows are the employees
  (Name + Designation); each day holds a shift code.
- Shift codes (code, times, paid hours, duty day yes/no) are kept by the
  HR Administrator. Total Hours / Total Days come from them.
- Prepared by the section/unit Supervisor (incl. OIC) or by HR. Routing,
  whole schedule only:
      Prepared -> Reviewed (HR) -> Recommending approval (AO)
      -> Approved (COH) -> Recorded (HR)
  Return needs a remark; a returned schedule is edited and resubmitted and
  starts again at HR review. The preparer never acts on it at a later step.
- COH approval date/approver = the "schedule approval date" Exchange of
  Duty uses. From COH approval the schedule is locked; it is shown to
  staff ("My Schedule") once HR records it.
- After approval, cells change only through an approved Exchange of Duty
  or an HR correction with a reason; every change is kept in
  ScheduleChange (the original stays in the history).
- Schedules are planned shifts, not attendance.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ShiftCode(models.Model):
    code = models.CharField(max_length=10, unique=True, help_text="As written on the schedule, e.g. 7A-7P.")
    description = models.CharField(max_length=100, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True, help_text="Earlier than the start = ends the next day.")
    paid_hours = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    is_duty = models.BooleanField("Counts as a duty day", default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.code


class DutySchedule(models.Model):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REVIEWED = "REVIEWED"
    RECOMMENDED = "RECOMMENDED"
    APPROVED = "APPROVED"
    RECORDED = "RECORDED"
    RETURNED = "RETURNED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted (for HR review)"),
        (REVIEWED, "Reviewed by HR"),
        (RECOMMENDED, "Recommended by AO"),
        (APPROVED, "Approved by COH"),
        (RECORDED, "Recorded by HR"),
        (RETURNED, "Returned"),
    ]
    EDITABLE = {DRAFT, RETURNED}
    LOCKED = {APPROVED, RECORDED}

    FILER_SUPERVISOR = "SUPERVISOR"
    FILER_HR = "HR"
    FILER_CHOICES = [(FILER_SUPERVISOR, "Supervisor"), (FILER_HR, "HR")]

    section = models.ForeignKey("orgstructure.Section", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="duty_schedules")
    unit = models.ForeignKey("orgstructure.Unit", on_delete=models.PROTECT, null=True, blank=True,
                             related_name="duty_schedules")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=DRAFT)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    preparer_role = models.CharField(max_length=10, choices=FILER_CHOICES)
    notes = models.TextField("Notes / remarks", blank=True, default="Lunch break hours 12:00NN-1:00PM")
    cutoff_date = models.DateField(
        help_text="Exchange of Duty requests for this month, filed before the schedule is approved, must be "
                  "filed at least 7 days before this date. Set by HR (default: the 20th of the month before).")
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    recorded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["section", "year", "month"], condition=models.Q(unit__isnull=True),
                                    name="one_schedule_per_section_month"),
            models.UniqueConstraint(fields=["unit", "year", "month"], condition=models.Q(unit__isnull=False),
                                    name="one_schedule_per_unit_month"),
        ]

    def __str__(self):
        return f"{self.area_name} — {self.month_label}"

    def clean(self):
        if bool(self.section_id) == bool(self.unit_id):
            raise ValidationError("Choose a section OR a unit.")

    @property
    def area_name(self):
        return self.unit.name if self.unit_id else self.section.name

    @property
    def area_full_name(self):
        return str(self.unit) if self.unit_id else self.section.name

    @property
    def month_label(self):
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def days(self):
        n = calendar.monthrange(self.year, self.month)[1]
        return [date(self.year, self.month, d) for d in range(1, n + 1)]

    @property
    def is_editable(self):
        return self.status in self.EDITABLE

    @property
    def is_locked(self):
        return self.status in self.LOCKED

    @property
    def is_published(self):
        return self.status == self.RECORDED

    @staticmethod
    def default_cutoff(year, month):
        prev = date(year, month, 1) - timedelta(days=1)
        return date(prev.year, prev.month, min(getattr(settings, "SCHEDULE_CUTOFF_DAY", 20), prev.day))

    def area_employees(self):
        """Active employees attached to this section/unit."""
        from employees.models import Employee

        qs = Employee.objects.filter(is_active=True, archived_at__isnull=True)
        return qs.filter(units=self.unit) if self.unit_id else qs.filter(sections=self.section)


class ScheduleRow(models.Model):
    schedule = models.ForeignKey(DutySchedule, on_delete=models.CASCADE, related_name="rows")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="schedule_rows")
    designation = models.CharField(max_length=150, blank=True, help_text="Position at the time of the schedule.")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "employee__surname", "employee__first_name"]
        constraints = [models.UniqueConstraint(fields=["schedule", "employee"], name="one_row_per_employee")]

    def __str__(self):
        return f"{self.schedule}: {self.employee}"


class ScheduleCell(models.Model):
    row = models.ForeignKey(ScheduleRow, on_delete=models.CASCADE, related_name="cells")
    date = models.DateField()
    shift = models.ForeignKey(ShiftCode, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["row", "date"], name="one_cell_per_row_day")]


class ScheduleAction(models.Model):
    schedule = models.ForeignKey(DutySchedule, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=15, choices=DutySchedule.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at", "pk"]


class ScheduleChange(models.Model):
    """A cell changed after COH approval (approved exchange or HR correction)."""

    schedule = models.ForeignKey(DutySchedule, on_delete=models.CASCADE, related_name="changes")
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    old_shift = models.CharField(max_length=10, blank=True)
    new_shift = models.CharField(max_length=10, blank=True)
    reason = models.CharField(max_length=255)
    exchange = models.ForeignKey("exchange.DutyExchangeRequest", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="schedule_changes")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["changed_at", "pk"]
