"""
Exchange of Duty routing reuses the same Supervisor/HR/AO/COH role checks
as leave and CTO routing — one role definition for the whole system.
"""

from leave.permissions import (  # noqa: F401 (re-exported for exchange callers)
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    UNIT_STEP,
    can_endorse,
    can_endorse_second,
    second_endorsers_for,
    supervisor_step_result,
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
    is_supervisor_of,
)


def is_party_to(acting_employee, request):
    return acting_employee is not None and acting_employee.pk in (request.employee_a_id, request.employee_b_id)


def is_supervisor_of_either_party(acting_employee, request, viewing=False):
    """A Supervisor of either employee_a or employee_b may endorse — the
    two employees have already given mutual consent by this point, so
    either party's supervisor endorsing is sufficient, keeping this in
    step with how a single Supervisor endorses a single applicant
    elsewhere in the system."""
    return is_supervisor_of(acting_employee, request.employee_a, viewing) or is_supervisor_of(
        acting_employee, request.employee_b, viewing
    )


def can_view_exchange_request(acting_employee, request):
    """The same visibility rule as leave/CTO: either party, HR, AO, COH, or
    a supervisor of either party."""
    if acting_employee is None:
        return False
    if is_party_to(acting_employee, request):
        return True
    if is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True) \
            or is_chief_of_hospital(acting_employee, True):
        return True
    return is_supervisor_of_either_party(acting_employee, request, viewing=True)


def visible_exchange_requests_for(acting_employee):
    """Requests this user may act on right now — mirrors
    cto.permissions.visible_cto_applications_for."""
    from .models import DutyExchangeRequest

    if acting_employee is None:
        return DutyExchangeRequest.objects.none()

    qs = DutyExchangeRequest.objects.none()

    if is_hr(acting_employee):
        qs = qs | DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.ENDORSED_BY_SUPERVISOR)

    ao_ids = [
        req.pk for req in DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.PROCESSED_BY_HR)
        .select_related("employee_a", "employee_b")
        if can_act_ao_step(acting_employee, [req.employee_a, req.employee_b])
    ]
    qs = qs | DutyExchangeRequest.objects.filter(pk__in=ao_ids)

    if is_chief_of_hospital(acting_employee):
        qs = qs | DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.RECOMMENDED_BY_AO)

    supervisor_candidates = DutyExchangeRequest.objects.filter(
        status=DutyExchangeRequest.SUBMITTED
    ).select_related("employee_a", "employee_b")
    supervisor_ids = [
        req.pk for req in supervisor_candidates if can_endorse(acting_employee, [req.employee_a, req.employee_b])
    ]
    qs = qs | DutyExchangeRequest.objects.filter(pk__in=supervisor_ids)

    # Second endorsement (section head), after the unit head - 2026-10-27.
    second_ids = [
        req.pk for req in DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.ENDORSED_BY_UNIT_HEAD)
        .select_related("employee_a", "employee_b")
        if can_endorse_second(acting_employee, [req.employee_a, req.employee_b])
    ]
    qs = qs | DutyExchangeRequest.objects.filter(pk__in=second_ids)

    # Never one's own exchange, at any step (owner decision 2026-10-26).
    return qs.exclude(employee_a=acting_employee).exclude(employee_b=acting_employee).distinct()
