"""
Exchange of Duty routing reuses the same Supervisor/HR/AO/COH role checks
as leave and CTO routing — one role definition for the whole system.
"""

from leave.permissions import (  # noqa: F401 (re-exported for exchange callers)
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    exclude_own_endorsements,
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


# -- Cross-unit exchange: both unit heads endorse (owner decision 2026-11-26) --
# Each side's unit head comes from the shared lookup (leave.permissions,
# so OIC and next-level-up rules apply), run for that party alone. A party
# who is a unit head has no unit step on their side (their side goes to the
# section head, as before); a side whose unit has no head has none either.
# Only when BOTH sides have a unit head does the unit step need two
# endorsements; otherwise the existing single-endorsement route applies.

def unit_sides(request):
    """{"A": [unit heads], "B": [unit heads]} for a cross-unit request filed
    under the new rules where both sides have a unit head; else {}."""
    from leave.permissions import _unit_level_endorsers

    from .schedule_checks import is_cross_unit

    a, b = request.employee_a, request.employee_b
    if not request.new_rules or not is_cross_unit(a, b):
        return {}
    sides = {}
    for side, party, other in (("A", a, b), ("B", b, a)):
        heads = [s for s in _unit_level_endorsers([party]) if s.pk != other.pk]
        if heads:
            sides[side] = heads
    return sides if len(sides) == 2 else {}


def side_heads_ids(request, side):
    return {e.pk for e in unit_sides(request).get(side, [])}


def remaining_side(request):
    return {"A": "B", "B": "A"}.get(request.first_unit_side, "")


def can_endorse_remaining_side(acting_employee, request):
    """May give the second unit-head endorsement of a cross-unit request."""
    if acting_employee is None or is_party_to(acting_employee, request):
        return False
    return acting_employee.pk in side_heads_ids(request, remaining_side(request))


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

    # Cross-unit: the other side's unit head, after the first one (2026-11-26).
    other_side = [
        req.pk for req in DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.ENDORSED_BY_ONE_UNIT_HEAD)
        .select_related("employee_a", "employee_b")
        if can_endorse_remaining_side(acting_employee, req)
    ]
    qs = qs | DutyExchangeRequest.objects.filter(pk__in=other_side)

    # Second endorsement (section head), after the unit head - 2026-10-27.
    second_ids = [
        req.pk for req in DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.ENDORSED_BY_UNIT_HEAD)
        .select_related("employee_a", "employee_b")
        if can_endorse_second(acting_employee, [req.employee_a, req.employee_b])
    ]
    qs = qs | DutyExchangeRequest.objects.filter(pk__in=second_ids)

    # Never one's own exchange, at any step (owner decision 2026-10-26),
    # and never at the HR step after endorsing it (2026-11-03).
    qs = exclude_own_endorsements(qs, acting_employee)
    return qs.exclude(employee_a=acting_employee).exclude(employee_b=acting_employee).distinct()
