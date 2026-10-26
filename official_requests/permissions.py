"""Reuses the same Supervisor/HR/AO/COH role checks as every other routed
request type in this system."""

from leave.permissions import (  # noqa: F401 (re-exported for callers)
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    can_endorse,
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
    is_supervisor_of,
)

from .models import OfficialRequest
from .routing import next_status


def can_view_request(acting_employee, official_request):
    if acting_employee is None:
        return False
    if acting_employee.pk == official_request.employee_id:
        return True
    if is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True) \
            or is_chief_of_hospital(acting_employee, True):
        return True
    return is_supervisor_of(acting_employee, official_request.employee, viewing=True)


def actor_may_advance(acting_employee, official_request):
    """True if acting_employee is the right role to take the next routing
    step on this request, whatever that step turns out to be."""
    if official_request.batch_id is not None:
        return False  # acted on through its group (batch) request
    target_status = next_status(official_request.request_type, official_request.status)
    if target_status is None:
        return False
    # Never on one's own request (owner decision 2026-10-26).
    filers = [official_request.employee]
    if target_status == OfficialRequest.ENDORSED_BY_SUPERVISOR:
        return can_endorse(acting_employee, filers)
    if target_status == OfficialRequest.PROCESSED_BY_HR:
        return can_act_hr_step(acting_employee, filers)
    if target_status == OfficialRequest.RECOMMENDED_BY_AO:
        return can_act_ao_step(acting_employee, filers)
    if target_status == OfficialRequest.APPROVED:
        return can_act_coh_step(acting_employee, filers)
    return False


def visible_requests_for(acting_employee):
    """Requests this user may act on right now, across all four types."""
    if acting_employee is None:
        return OfficialRequest.objects.none()

    # Lines of a group (batch) request are acted on through the batch only.
    candidates = OfficialRequest.objects.exclude(status__in=OfficialRequest.TERMINAL_STATUSES).filter(
        batch__isnull=True).select_related("employee")
    matching_ids = [req.pk for req in candidates if actor_may_advance(acting_employee, req)]
    return OfficialRequest.objects.filter(pk__in=matching_ids)
