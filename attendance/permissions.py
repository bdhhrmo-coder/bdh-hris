"""
Attendance Correction routing. FORMAL requests follow the Missed Log
Justification Form (BDH-ADM-AO-01F10, owner decision 2026-10-06): ICTU
Staff or HR validates depending on the reason, then the AO approves. MINOR
requests keep the HR Processor -> HR Administrator path (§3: "HR
Administrator ... authorizes HR data corrections").
"""

from accounts.models import RoleAssignment
from leave.permissions import (  # noqa: F401 (re-exported for attendance callers)
    ao_step_actors,
    can_act_ao_step,
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


def filers_of(correction_request):
    """Nobody acts on a correction of their own attendance, or on one they
    filed (owner decision 2026-10-26)."""
    people = [correction_request.employee]
    filer = getattr(correction_request.filed_by, "employee", None) if correction_request.filed_by_id else None
    if filer is not None:
        people.append(filer)
    return people


def _not_filer(acting_employee, correction_request):
    return acting_employee is not None and acting_employee.pk not in {p.pk for p in filers_of(correction_request)}


def can_view_correction_request(acting_employee, correction_request):
    if acting_employee is None:
        return False
    if acting_employee.pk == correction_request.employee_id:
        return True
    if is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True):
        return True
    if correction_request.validated_by_ictu and is_ictu_staff(acting_employee):
        return True
    return is_supervisor_of(acting_employee, correction_request.employee, viewing=True)


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

    ao_ids = [
        c.pk for c in formal.filter(
            status__in=[AttendanceCorrectionRequest.VALIDATED, AttendanceCorrectionRequest.PROCESSED_BY_HR]
        ).select_related("employee", "filed_by")
        if can_act_ao_step(acting_employee, filers_of(c))
    ]
    qs = qs | AttendanceCorrectionRequest.objects.filter(pk__in=ao_ids)

    # Never one's own correction, or one this person filed (owner decision 2026-10-26).
    qs = qs.exclude(employee=acting_employee)
    if acting_employee.user_id:
        qs = qs.exclude(filed_by_id=acting_employee.user_id)
    return qs.distinct()


def available_actions(acting_employee, correction):
    """[(action, button label)] this person may take on the WHOLE request
    right now. Return needs a remark (checked in the view's form)."""
    R = AttendanceCorrectionRequest
    if acting_employee is None:
        return []
    status = correction.status
    stop = [("return", "Return"), ("reject", "Reject")]
    if not _not_filer(acting_employee, correction):
        return []  # never on one's own (owner decision 2026-10-26)
    filers = filers_of(correction)
    if correction.correction_type == R.MINOR:
        if status == R.SUBMITTED and is_hr_administrator(acting_employee):
            return [("approve", "Authorize")] + stop
        return []
    if status == R.SUBMITTED and is_validator_for(acting_employee, correction):
        return [("validate", "Validate")] + stop
    if status == R.VALIDATED and can_act_ao_step(acting_employee, filers):
        return [("approve", "Approve")] + stop
    # Old Supervisor -> HR -> AO chain, only for requests endorsed before 2026-10-06.
    if status == R.ENDORSED_BY_SUPERVISOR and is_hr(acting_employee):
        return [("process", "Process")] + stop
    if status == R.PROCESSED_BY_HR and can_act_ao_step(acting_employee, filers):
        return [("approve", "Approve")] + stop
    return []
