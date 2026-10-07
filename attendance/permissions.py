"""
Attendance Correction routing. FORMAL requests follow the Missed Log
Justification Form (BDH-ADM-AO-01F10, owner decision 2026-10-06): ICTU
Staff or HR validates depending on the reason, then the AO approves. MINOR
requests keep the HR Processor -> HR Administrator path (§3: "HR
Administrator ... authorizes HR data corrections").
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


def is_ictu_staff(acting_employee):
    return acting_employee is not None and acting_employee.has_role(RoleAssignment.ICTU_STAFF)


def is_validator_for(acting_employee, correction_request):
    """FORMAL corrections (BDH-ADM-AO-01F10): ICTU Staff validate the
    biometric/system reasons, HR validates the rest."""
    if correction_request.validated_by_ictu:
        return is_ictu_staff(acting_employee)
    return is_hr(acting_employee)


def can_view_correction_request(acting_employee, correction_request):
    if acting_employee is None:
        return False
    if acting_employee.pk == correction_request.employee_id:
        return True
    if is_hr(acting_employee) or is_administrative_officer(acting_employee):
        return True
    if correction_request.validated_by_ictu and is_ictu_staff(acting_employee):
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

    formal = AttendanceCorrectionRequest.objects.filter(correction_type=AttendanceCorrectionRequest.FORMAL)
    ictu = AttendanceCorrectionRequest.VALIDATOR_ICTU

    if is_ictu_staff(acting_employee):
        qs = qs | formal.filter(status=AttendanceCorrectionRequest.SUBMITTED, validator=ictu)

    if is_hr(acting_employee):
        qs = qs | formal.filter(status=AttendanceCorrectionRequest.SUBMITTED).exclude(validator=ictu)
        # Old Supervisor -> HR -> AO chain: let requests already endorsed finish.
        qs = qs | formal.filter(status=AttendanceCorrectionRequest.ENDORSED_BY_SUPERVISOR)

    if is_administrative_officer(acting_employee):
        qs = qs | formal.filter(
            status__in=[AttendanceCorrectionRequest.VALIDATED, AttendanceCorrectionRequest.PROCESSED_BY_HR]
        )

    return qs.distinct()


def available_actions(acting_employee, correction):
    """[(action, button label)] this person may take on the WHOLE request
    right now. Return needs a remark (checked in the view's form)."""
    R = AttendanceCorrectionRequest
    if acting_employee is None:
        return []
    status = correction.status
    stop = [("return", "Return"), ("reject", "Reject")]
    if correction.correction_type == R.MINOR:
        if status == R.SUBMITTED and is_hr_administrator(acting_employee):
            return [("approve", "Authorize")] + stop
        return []
    if status == R.SUBMITTED and is_validator_for(acting_employee, correction):
        return [("validate", "Validate")] + stop
    if status == R.VALIDATED and is_administrative_officer(acting_employee):
        return [("approve", "Approve")] + stop
    # Old Supervisor -> HR -> AO chain, only for requests endorsed before 2026-10-06.
    if status == R.ENDORSED_BY_SUPERVISOR and is_hr(acting_employee):
        return [("process", "Process")] + stop
    if status == R.PROCESSED_BY_HR and is_administrative_officer(acting_employee):
        return [("approve", "Approve")] + stop
    return []
