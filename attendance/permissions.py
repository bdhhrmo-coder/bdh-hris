"""
Attendance Correction routing reuses the same Supervisor/HR/AO role checks
as leave/CTO/exchange, plus HR Processor vs HR Administrator distinctions
that §3 draws specifically for this module ("HR Administrator ...
authorizes HR data corrections").
"""

from accounts.models import RoleAssignment
from leave.permissions import (  # noqa: F401 (re-exported for attendance callers)
    get_acting_employee,
    is_administrative_officer,
    is_hr,
    is_supervisor_of,
)

from .models import AttendanceCorrectionRequest


def is_hr_processor(acting_employee):
    return acting_employee is not None and acting_employee.has_role(RoleAssignment.HR_PROCESSOR)


def is_hr_administrator(acting_employee):
    return acting_employee is not None and acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)


def can_view_correction_request(acting_employee, correction_request):
    if acting_employee is None:
        return False
    if acting_employee.pk == correction_request.employee_id:
        return True
    if is_hr(acting_employee) or is_administrative_officer(acting_employee):
        return True
    return is_supervisor_of(acting_employee, correction_request.employee)


def visible_correction_requests_for(acting_employee):
    """Requests this user may act on right now."""
    if acting_employee is None:
        return AttendanceCorrectionRequest.objects.none()

    qs = AttendanceCorrectionRequest.objects.none()

    if is_hr_administrator(acting_employee):
        qs = qs | AttendanceCorrectionRequest.objects.filter(
            correction_type=AttendanceCorrectionRequest.MINOR, status=AttendanceCorrectionRequest.SUBMITTED
        )

    if is_hr(acting_employee):
        qs = qs | AttendanceCorrectionRequest.objects.filter(
            correction_type=AttendanceCorrectionRequest.FORMAL,
            status=AttendanceCorrectionRequest.ENDORSED_BY_SUPERVISOR,
        )

    if is_administrative_officer(acting_employee):
        qs = qs | AttendanceCorrectionRequest.objects.filter(
            correction_type=AttendanceCorrectionRequest.FORMAL, status=AttendanceCorrectionRequest.PROCESSED_BY_HR
        )

    supervisor_candidates = AttendanceCorrectionRequest.objects.filter(
        correction_type=AttendanceCorrectionRequest.FORMAL, status=AttendanceCorrectionRequest.SUBMITTED
    ).select_related("employee")
    supervisor_ids = [
        req.pk for req in supervisor_candidates if is_supervisor_of(acting_employee, req.employee)
    ]
    qs = qs | AttendanceCorrectionRequest.objects.filter(pk__in=supervisor_ids)

    return qs.distinct()
