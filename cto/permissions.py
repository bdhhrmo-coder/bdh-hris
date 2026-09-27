"""
CTO usage routing reuses the exact same role checks as leave routing
(is_hr, is_administrative_officer, is_chief_of_hospital, is_supervisor_of)
rather than re-implementing them — one Supervisor/HR/AO/COH definition for
the whole system, not two copies that could drift apart.
"""

from leave.permissions import (  # noqa: F401 (re-exported for cto callers)
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
    is_supervisor_of,
)


def is_cto_eligible(employee):
    """CTO is not applicable to the Chief of Hospital (CLAUDE.md §7)."""
    return employee is not None and not employee.has_role("CHIEF_OF_HOSPITAL")


def can_view_cto_application(acting_employee, application):
    """Same visibility rule as leave.permissions.can_view_application:
    the applicant, HR, AO, COH, or the applicant's supervisor."""
    if acting_employee is None:
        return False
    if acting_employee.pk == application.employee_id:
        return True
    if is_hr(acting_employee) or is_administrative_officer(acting_employee) or is_chief_of_hospital(acting_employee):
        return True
    return is_supervisor_of(acting_employee, application.employee)


def visible_cto_applications_for(acting_employee):
    """Applications this user may act on right now — mirrors
    leave.permissions.visible_applications_for, but CTO usage is always
    full-routing so there's no data-entry-only branch to special-case."""
    from .models import CTOUsageApplication

    if acting_employee is None:
        return CTOUsageApplication.objects.none()

    qs = CTOUsageApplication.objects.none()

    if is_hr(acting_employee):
        qs = qs | CTOUsageApplication.objects.filter(status=CTOUsageApplication.ENDORSED_BY_SUPERVISOR)

    if is_administrative_officer(acting_employee):
        qs = qs | CTOUsageApplication.objects.filter(status=CTOUsageApplication.PROCESSED_BY_HR)

    if is_chief_of_hospital(acting_employee):
        qs = qs | CTOUsageApplication.objects.filter(status=CTOUsageApplication.RECOMMENDED_BY_AO)

    supervisor_candidates = CTOUsageApplication.objects.filter(
        status=CTOUsageApplication.SUBMITTED
    ).select_related("employee")
    supervisor_ids = [
        app.pk for app in supervisor_candidates if is_supervisor_of(acting_employee, app.employee)
    ]
    qs = qs | CTOUsageApplication.objects.filter(pk__in=supervisor_ids)

    return qs.distinct()
