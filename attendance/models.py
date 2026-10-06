"""
Attendance & biometric integration (CLAUDE.md §9) and Attendance
Correction (§6.2, §3).

Confirmed with the project owner on 2026-09-27:
  - One time-in/time-out pair per employee per day (a standard CSC Daily
    Time Record summary), not per-punch AM/PM detail.
  - The biometric CSV/Excel column layout isn't known yet (BDH's actual
    device/software wasn't specified and no sample export was available),
    so BiometricColumnMapping makes the column names/formats configurable
    in Django admin rather than hardcoded, and importer.py documents the
    default it ships with.
  - "Undertime auto-deducts from leave credits" charges Vacation Leave
    (VL) specifically; if VL balance is insufficient the shortfall is
    simply recorded (no payroll deduction — out of scope).
  - The "minor administrative" vs "formal" Attendance Correction paths
    (§6.2) are both modeled as AttendanceCorrectionRequest, distinguished
    by correction_type, mirroring how leave.LeaveType.requires_full_routing
    splits COSP Leave from every other leave type in that app:
      MINOR: HR Processor files it directly (on the employee's behalf,
        not the employee) -> HR Administrator authorizes it. No
        Supervisor/AO step (§3: "HR Administrator ... authorizes HR data
        corrections"). If an HR Administrator files it themselves, it is
        auto-approved — there is no reason to route it back to the same
        role.
      FORMAL (changed 2026-10-06 by the project owner, to follow the
        Missed Log Justification Form, BDH-ADM-AO-01F10): the employee
        files it with a reason category -> ONE validator, chosen by that
        category -> AO approves (final; no COH step).
          Offline / Failed Attempt / Wrong Button-Invalid Entry
              -> ICTU Staff validates (a biometric/system problem)
          Attended meeting-activity-training / Others
              -> HR validates
        There is no Supervisor step (F10 has none). Requests already
        endorsed or processed under the old Employee -> Supervisor -> HR ->
        AO chain finish on that chain (ENDORSED_BY_SUPERVISOR /
        PROCESSED_BY_HR are kept for them).

Biometric data is advisory, not authoritative (§9): once an
AttendanceRecord has been hand-corrected (source=MANUAL), a later
biometric import for that same employee/date is skipped rather than
silently overwriting it — see importer.py.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class BiometricColumnMapping(models.Model):
    """
    Configurable column layout for a biometric CSV/Excel export, so the
    importer can be pointed at BDH's real export format later without a
    code change. Exactly one mapping should be is_active at a time —
    enforced in save(), not just left to convention.
    """

    name = models.CharField(max_length=100, unique=True)
    employee_id_column = models.CharField(
        max_length=100, default="Employee ID",
        help_text="Column header holding the value that matches Employee.employee_id.",
    )
    date_column = models.CharField(max_length=100, default="Date")
    time_in_column = models.CharField(max_length=100, default="Time In")
    time_out_column = models.CharField(max_length=100, default="Time Out")
    date_format = models.CharField(max_length=50, default="%Y-%m-%d", help_text="Python strptime format.")
    time_format = models.CharField(max_length=50, default="%H:%M:%S", help_text="Python strptime format.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}{' (active)' if self.is_active else ''}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            BiometricColumnMapping.objects.exclude(pk=self.pk).update(is_active=False)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()


class BiometricImportBatch(models.Model):
    """One HR-initiated batch upload of a biometric CSV/Excel export."""

    original_filename = models.CharField(max_length=255)
    mapping_used = models.ForeignKey(
        BiometricColumnMapping, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    row_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(
        default=0, help_text="Rows skipped because that employee/date was already manually corrected."
    )
    error_count = models.PositiveIntegerField(default=0)
    error_log = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.original_filename} ({self.uploaded_at:%Y-%m-%d %H:%M}) — {self.imported_count} imported"


class AttendanceRecord(models.Model):
    """
    One employee's attendance for one date. Whichever of BIOMETRIC or
    MANUAL wrote it last, this is the single row HR and reports read from
    — no separate "corrected" overlay table.
    """

    SOURCE_BIOMETRIC = "BIOMETRIC"
    SOURCE_MANUAL = "MANUAL"
    SOURCE_CHOICES = [
        (SOURCE_BIOMETRIC, "Biometric import"),
        (SOURCE_MANUAL, "Manually encoded/corrected"),
    ]

    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    time_in = models.TimeField(null=True, blank=True)
    time_out = models.TimeField(null=True, blank=True)
    is_absent = models.BooleanField(
        default=False,
        help_text="Only ever set by a manual/correction entry — a missing biometric punch on its own "
        "doesn't imply absence, since this system has no duty-schedule module to confirm the employee "
        "was expected to work that day.",
    )
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_BIOMETRIC)
    undertime_minutes = models.PositiveIntegerField(default=0)
    import_batch = models.ForeignKey(
        BiometricImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="records"
    )
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_record_per_employee_per_day")
        ]

    def __str__(self):
        return f"{self.employee} — {self.date} ({self.get_source_display()})"

    def clean(self):
        if self.time_in and self.time_out is None and not self.is_absent:
            pass  # a single open punch (still clocked in) is valid, not an error
        if self.is_absent and (self.time_in or self.time_out):
            raise ValidationError("An attendance record marked Absent cannot also carry time in/out values.")

    def recompute_undertime(self):
        """
        Minutes short of a full shift (employees.Employee.shift_hours),
        given this record's time_in/time_out. Handles a shift that crosses
        midnight (time_out earlier than time_in) by treating time_out as
        the next day. Absence isn't charged as undertime here — that's a
        separate, out-of-scope leave/attendance reconciliation.
        """
        if self.is_absent or not self.time_in or not self.time_out:
            self.undertime_minutes = 0
            return
        start = timedelta(hours=self.time_in.hour, minutes=self.time_in.minute, seconds=self.time_in.second)
        end = timedelta(hours=self.time_out.hour, minutes=self.time_out.minute, seconds=self.time_out.second)
        if end < start:
            end += timedelta(days=1)
        worked_minutes = (end - start).total_seconds() / 60
        expected_minutes = self.employee.shift_hours * 60
        self.undertime_minutes = max(0, int(expected_minutes - worked_minutes))

    def save(self, *args, **kwargs):
        self.recompute_undertime()
        super().save(*args, **kwargs)


class AttendanceCorrectionRequest(models.Model):
    """See module docstring for the MINOR vs FORMAL distinction and why
    both share one model."""

    MINOR = "MINOR"
    FORMAL = "FORMAL"
    CORRECTION_TYPE_CHOICES = [
        (MINOR, "Minor administrative (HR-initiated)"),
        (FORMAL, "Formal (employee-initiated)"),
    ]

    SUBMITTED = "SUBMITTED"
    VALIDATED = "VALIDATED"
    ENDORSED_BY_SUPERVISOR = "ENDORSED_BY_SUPERVISOR"  # old chain only
    PROCESSED_BY_HR = "PROCESSED_BY_HR"  # old chain only
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [
        (SUBMITTED, "Submitted"),
        (VALIDATED, "Validated"),
        (ENDORSED_BY_SUPERVISOR, "Endorsed by Supervisor"),
        (PROCESSED_BY_HR, "Processed by HR"),
        (APPROVED, "Approved"),
        (RETURNED, "Returned"),
        (REJECTED, "Rejected"),
    ]

    # Reason categories, exactly as on the Missed Log Justification Form
    # (BDH-ADM-AO-01F10). * = validated by ICTU Staff, ** = by HR.
    REASON_OFFLINE = "OFFLINE"
    REASON_FAILED_ATTEMPT = "FAILED_ATTEMPT"
    REASON_WRONG_ENTRY = "WRONG_ENTRY"
    REASON_ATTENDED_ACTIVITY = "ATTENDED_ACTIVITY"
    REASON_OTHERS = "OTHERS"
    REASON_CATEGORY_CHOICES = [
        (REASON_OFFLINE, "Offline"),
        (REASON_FAILED_ATTEMPT, "Failed Attempt"),
        (REASON_WRONG_ENTRY, "Wrong Button Selected / Invalid Entry"),
        (REASON_ATTENDED_ACTIVITY, "Attended meeting / activity / training / others (specify)"),
        (REASON_OTHERS, "Others (specify)"),
    ]
    ICTU_VALIDATED_REASONS = {REASON_OFFLINE, REASON_FAILED_ATTEMPT, REASON_WRONG_ENTRY}
    REASONS_NEEDING_DETAILS = {REASON_ATTENDED_ACTIVITY, REASON_OTHERS}

    correction_type = models.CharField(max_length=10, choices=CORRECTION_TYPE_CHOICES)
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="attendance_correction_requests"
    )
    date = models.DateField(help_text="The attendance date being corrected.")
    requested_time_in = models.TimeField(null=True, blank=True)
    requested_time_out = models.TimeField(null=True, blank=True)
    requested_is_absent = models.BooleanField(
        default=False, help_text="Check to correct this date to Absent instead of supplying times."
    )
    reason_category = models.CharField(
        "Reason", max_length=20, choices=REASON_CATEGORY_CHOICES, default=REASON_OTHERS,
        help_text="As on the Missed Log Justification Form. Decides who validates (ICTU or HR).",
    )
    reason = models.CharField(
        "Details", max_length=255, blank=True,
        help_text="Required for 'Attended meeting/activity/training' and 'Others': say what it was.",
    )
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=SUBMITTED)
    filed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
        help_text="Who filed this — the employee themselves for FORMAL, or the HR Processor/Administrator for MINOR.",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.get_correction_type_display()} — {self.employee} {self.date} ({self.status})"

    @property
    def validated_by_ictu(self):
        return self.reason_category in self.ICTU_VALIDATED_REASONS

    @property
    def validator_label(self):
        return "ICTU Staff" if self.validated_by_ictu else "HR Staff"

    def clean(self):
        if not self.reason.strip():
            if self.correction_type == self.MINOR:
                raise ValidationError({"reason": "Give the reason for this correction."})
            if self.reason_category in self.REASONS_NEEDING_DETAILS:
                raise ValidationError({"reason": "Please specify the meeting/activity/training or other reason."})
        if self.requested_is_absent and (self.requested_time_in or self.requested_time_out):
            raise ValidationError("Check 'mark as absent' OR give time in/out, not both.")
        if not self.requested_is_absent and not self.requested_time_in and not self.requested_time_out:
            raise ValidationError("Provide requested time in/out, or check 'mark as absent'.")


class AttendanceCorrectionRequestAction(models.Model):
    """Accountability log per routing step — same shape as every other
    request-action model in this system."""

    request = models.ForeignKey(AttendanceCorrectionRequest, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=25, choices=AttendanceCorrectionRequest.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at"]

    def __str__(self):
        return f"{self.request} — {self.action} @ {self.acted_at:%Y-%m-%d %H:%M}"
