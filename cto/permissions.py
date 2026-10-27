"""
CTO usage routing reuses the exact same role checks as leave routing
(is_hr, is_administrative_officer, is_chief_of_hospital, is_supervisor_of)
rather than re-implementing them — one Supervisor/HR/AO/COH definition for
the whole system, not two copies that could drift apart.
"""

from leave.permissions import (  # noqa: F401 (re-exported for cto callers)
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
    if is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True) \
            or is_chief_of_hospital(acting_employee, True):
        return True
    return is_supervisor_of(acting_employee, application.employee, viewing=True)


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

    ao_ids = [
        app.pk for app in CTOUsageApplication.objects.filter(status=CTOUsageApplication.PROCESSED_BY_HR)
        .select_related("employee") if can_act_ao_step(acting_employee, [app.employee])
    ]
    qs = qs | CTOUsageApplication.objects.filter(pk__in=ao_ids)

    if is_chief_of_hospital(acting_employee):
        qs = qs | CTOUsageApplication.objects.filter(status=CTOUsageApplication.RECOMMENDED_BY_AO)

    supervisor_candidates = CTOUsageApplication.objects.filter(
        status=CTOUsageApplication.SUBMITTED
    ).select_related("employee")
    supervisor_ids = [
        app.pk for app in supervisor_candidates if can_endorse(acting_employee, [app.employee])
    ]
    qs = qs | CTOUsageApplication.objects.filter(pk__in=supervisor_ids)

    # Second endorsement (section head), after the unit head - 2026-10-27.
    second_ids = [
        app.pk for app in CTOUsageApplication.objects.filter(status=CTOUsageApplication.ENDORSED_BY_UNIT_HEAD)
        .select_related("employee") if can_endorse_second(acting_employee, [app.employee])
    ]
    qs = qs | CTOUsageApplication.objects.filter(pk__in=second_ids)

    # Never one's own application, at any step (owner decision 2026-10-26).
    return qs.exclude(employee=acting_employee).distinct()
