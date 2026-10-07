"""Mirrors leave/notifications.py's shape (see that module's docstring for
the CLAUDE.md §11/§2 in-app-only-for-now rationale), split by
correction_type the same way attendance.views.correction_action is: the
MINOR path (HR Processor -> HR Administrator, no Supervisor/AO step) and
the FORMAL path (BDH-ADM-AO-01F10, 2026-10-06: ICTU Staff or HR validates,
depending on the reason, then the AO approves - no COH step)."""

from leave.permissions import employees_with_role, hr_employees
from notifications.services import notify

from .models import AttendanceCorrectionRequest

_QUEUE_URL = "/attendance/correction/queue/"
_MINE_URL = "/attendance/mine/"


def _detail_url(correction):
    return f"/attendance/correction/{correction.pk}/"


def _return_remark(correction):
    latest = correction.latest_return()
    return f' Remark: "{latest.notes}"' if latest and latest.notes else ""


def notify_status_change(correction):
    from accounts.models import RoleAssignment

    status = correction.status

    if correction.correction_type == AttendanceCorrectionRequest.MINOR:
        if status == AttendanceCorrectionRequest.SUBMITTED:
            notify(
                employees_with_role(RoleAssignment.HR_ADMINISTRATOR),
                f"A minor attendance correction for {correction.employee} needs authorization.",
                _QUEUE_URL,
            )
        elif status in (AttendanceCorrectionRequest.APPROVED, AttendanceCorrectionRequest.REJECTED):
            notify(
                correction.employee,
                f"Your attendance correction was {correction.get_status_display().lower()}.",
                _MINE_URL,
            )
        elif status == AttendanceCorrectionRequest.RETURNED:
            filer = getattr(correction.filed_by, "employee", None)
            notify(
                filer,
                f"The minor attendance correction you filed for {correction.employee} was returned."
                f"{_return_remark(correction)} Please edit and resubmit.",
                _detail_url(correction),
            )
    else:
        if status == AttendanceCorrectionRequest.SUBMITTED:
            validators = (
                employees_with_role(RoleAssignment.ICTU_STAFF) if correction.validated_by_ictu else hr_employees()
            )
            dates = correction.lines.count()
            notify(
                validators,
                f"{correction.employee} filed a missed log justification ({dates} date{'s' if dates != 1 else ''}) "
                "— awaiting your validation.",
                _detail_url(correction),
            )
        elif status == AttendanceCorrectionRequest.VALIDATED:
            notify(
                employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER),
                f"A validated attendance correction for {correction.employee} is awaiting your approval.",
                _QUEUE_URL,
            )
        elif status == AttendanceCorrectionRequest.ENDORSED_BY_SUPERVISOR:
            notify(
                hr_employees(),
                f"An attendance correction request for {correction.employee} is ready for HR processing.",
                _QUEUE_URL,
            )
        elif status == AttendanceCorrectionRequest.PROCESSED_BY_HR:
            notify(
                employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER),
                f"An attendance correction request for {correction.employee} is awaiting your approval.",
                _QUEUE_URL,
            )
        elif status in (AttendanceCorrectionRequest.APPROVED, AttendanceCorrectionRequest.REJECTED):
            notify(
                correction.employee,
                f"Your attendance correction request was {correction.get_status_display().lower()}.",
                _detail_url(correction),
            )
        elif status == AttendanceCorrectionRequest.RETURNED:
            notify(
                correction.employee,
                f"Your attendance correction request was returned.{_return_remark(correction)} "
                "Please edit and resubmit it.",
                _detail_url(correction),
            )
