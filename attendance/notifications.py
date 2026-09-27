"""Mirrors leave/notifications.py's shape (see that module's docstring for
the CLAUDE.md §11/§2 in-app-only-for-now rationale), split by
correction_type the same way attendance.views.correction_action is: the
MINOR path (HR Processor -> HR Administrator, no Supervisor/AO step) and
the FORMAL path (Supervisor -> HR -> AO, per §6.2 — no COH step for
attendance corrections)."""

from leave.permissions import employees_with_role, hr_employees, supervisors_of
from notifications.services import notify

from .models import AttendanceCorrectionRequest

_QUEUE_URL = "/attendance/correction/queue/"
_MINE_URL = "/attendance/mine/"


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
    else:
        if status == AttendanceCorrectionRequest.SUBMITTED:
            notify(
                supervisors_of(correction.employee),
                f"{correction.employee} filed an attendance correction request — awaiting your endorsement.",
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
        elif status in (
            AttendanceCorrectionRequest.APPROVED,
            AttendanceCorrectionRequest.REJECTED,
            AttendanceCorrectionRequest.RETURNED,
        ):
            notify(
                correction.employee,
                f"Your attendance correction request was {correction.get_status_display().lower()}.",
                _MINE_URL,
            )
