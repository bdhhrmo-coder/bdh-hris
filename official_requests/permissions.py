"""Reuses the same Supervisor/HR/AO/COH role checks as every other routed
request type in this system."""

from leave.permissions import (  # noqa: F401 (re-exported for callers)
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
    if is_hr(acting_employee) or is_administrative_officer(acting_employee) or is_chief_of_hospital(acting_employee):
        return True
    return is_supervisor_of(acting_employee, official_request.employee)


def actor_may_advance(acting_employee, official_request):
    """True if acting_employee is the right role to take the next routing
    step on this request, whatever that step turns out to be."""
    target_status = next_status(official_request.request_type, official_request.status)
    if target_status is None:
        return False
    if target_status == OfficialRequest.ENDORSED_BY_SUPERVISOR:
        return is_supervisor_of(acting_employee, official_request.employee)
    if target_status == OfficialRequest.PROCESSED_BY_HR:
        return is_hr(acting_employee)
    if target_status == OfficialRequest.RECOMMENDED_BY_AO:
        return is_administrative_officer(acting_employee)
    if target_status == OfficialRequest.APPROVED:
        return is_chief_of_hospital(acting_employee)
    return False


def visible_requests_for(acting_employee):
    """Requests this user may act on right now, across all four types."""
    if acting_employee is None:
        return OfficialRequest.objects.none()

    candidates = OfficialRequest.objects.exclude(status__in=OfficialRequest.TERMINAL_STATUSES).select_related(
        "employee"
    )
    matching_ids = [req.pk for req in candidates if actor_may_advance(acting_employee, req)]
    return OfficialRequest.objects.filter(pk__in=matching_ids)
