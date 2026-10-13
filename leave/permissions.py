"""
Leave routing permissions.

CRITICAL rule from CLAUDE.md §6.2: Regular Leave (and every other CSC-listed
type) must NEVER be routed through the internal approval engine — only COSP
Leave does. This is enforced structurally, not just by convention: every
queue/action helper below filters on leave_type.requires_full_routing so a
Supervisor/AO/COH simply cannot see or act on a non-COSP-Leave application,
regardless of its status.
"""

from accounts.models import RoleAssignment
from employees.permissions import get_acting_employee  # re-exported for convenience

from .models import LeaveApplication


# viewing=True (Batch 7, owner 2026-10-13): use in VIEW-ONLY checks. An
# approver who has handed an area to an OIC keeps view access to it but
# cannot act on it; action checks keep the default viewing=False.

def _has_active_role(acting_employee, role_code, viewing=False):
    if acting_employee is None:
        return False
    return acting_employee.has_role(role_code, viewing=viewing)


def is_hr(acting_employee, viewing=False):
    return _has_active_role(acting_employee, RoleAssignment.HR_PROCESSOR, viewing) or _has_active_role(
        acting_employee, RoleAssignment.HR_ADMINISTRATOR, viewing
    )


def is_administrative_officer(acting_employee, viewing=False):
    return _has_active_role(acting_employee, RoleAssignment.ADMINISTRATIVE_OFFICER, viewing)


def is_chief_of_hospital(acting_employee, viewing=False):
    return _has_active_role(acting_employee, RoleAssignment.CHIEF_OF_HOSPITAL, viewing)


def is_supervisor_of(acting_employee, target_employee, viewing=False):
    """
    True if acting_employee holds an active Supervisor (or OIC-as-Supervisor)
    RoleAssignment whose section/unit overlaps the target's assignments.
    """
    if acting_employee is None:
        return False
    target_section_ids = set(target_employee.sections.values_list("id", flat=True))
    target_unit_ids = set(target_employee.units.values_list("id", flat=True))
    for ra in acting_employee.active_role_assignments(viewing=viewing).filter(role=RoleAssignment.SUPERVISOR):
        if ra.section_id is None and ra.unit_id is None:
            return True  # hospital-wide supervisor assignment
        if ra.section_id and ra.section_id in target_section_ids:
            return True
        if ra.unit_id and ra.unit_id in target_unit_ids:
            return True
    return False


def employees_with_role(role_code):
    """
    All employees holding an active RoleAssignment of this role code.

    Used for notification fan-out (Phase 10, CLAUDE.md §11): the HR/AO/COH
    routing steps aren't assigned to a named individual per request — any
    holder of that role may act — so "who should be notified" for those
    steps is every current holder of the role, the same set implicitly
    used by is_hr/is_administrative_officer/is_chief_of_hospital above.
    """
    from employees.models import Employee

    # Batch 7: same test as has_role (dates in force, OIC hand-over), so an
    # OIC is notified only during the OIC period and the absent approver
    # is not notified for the area handed over.
    candidate_ids = set(RoleAssignment.objects.filter(role=role_code, is_active=True).values_list(
        "employee_id", flat=True
    ))
    employee_ids = [e.pk for e in Employee.objects.filter(pk__in=candidate_ids) if e.has_role(role_code)]
    return Employee.objects.filter(pk__in=employee_ids).distinct()


def hr_employees():
    """Every active HR Processor or HR Administrator — mirrors is_hr's
    either/or check, but returns the people instead of testing one."""
    return employees_with_role(RoleAssignment.HR_PROCESSOR) | employees_with_role(RoleAssignment.HR_ADMINISTRATOR)


def supervisors_of(target_employee):
    """
    Every employee who is an active Supervisor (or OIC-as-Supervisor) of
    target_employee's sections/units — mirrors is_supervisor_of's logic,
    but returns every matching supervisor instead of testing one candidate,
    for notification fan-out.
    """
    return [
        candidate
        for candidate in employees_with_role(RoleAssignment.SUPERVISOR)
        if is_supervisor_of(candidate, target_employee)
    ]


def can_submit_for(acting_employee, target_employee):
    """An employee may only submit a leave application for themselves."""
    return acting_employee is not None and acting_employee.pk == target_employee.pk


def can_view_application(acting_employee, application):
    """
    Who may open/print a given leave application: the applicant, anyone in
    its routing/monitoring chain (HR, AO, COH), or the applicant's
    supervisor — the same set of people who would ever see it in a queue
    or need to hand the applicant a copy of the form.
    """
    if acting_employee is None:
        return False
    if acting_employee.pk == application.employee_id:
        return True
    if is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True) \
            or is_chief_of_hospital(acting_employee, True):
        return True
    return is_supervisor_of(acting_employee, application.employee, viewing=True)


def visible_applications_for(acting_employee):
    """
    Applications this user may act on right now, scoped by role AND status.
    Used to build each role's queue — never used for the employee's own
    "my applications" list, which shows everything of theirs regardless of
    status.
    """
    if acting_employee is None:
        return LeaveApplication.objects.none()

    qs = LeaveApplication.objects.none()

    if is_hr(acting_employee):
        # HR acts on: SUBMITTED for data-entry-only types, plus
        # SUBMITTED/ENDORSED_BY_SUPERVISOR for COSP Leave.
        data_entry_only = LeaveApplication.objects.filter(
            leave_type__requires_full_routing=False, status=LeaveApplication.SUBMITTED
        )
        cosp_leave_stage = LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True,
            status__in=[LeaveApplication.SUBMITTED, LeaveApplication.ENDORSED_BY_SUPERVISOR],
        )
        qs = qs | data_entry_only | cosp_leave_stage

    if is_administrative_officer(acting_employee):
        qs = qs | LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True, status=LeaveApplication.PROCESSED_BY_HR
        )

    if is_chief_of_hospital(acting_employee):
        qs = qs | LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True, status=LeaveApplication.RECOMMENDED_BY_AO
        )

    # Supervisor queue is scoped per-employee, so it's filtered in Python
    # rather than a single queryset filter.
    supervisor_candidates = LeaveApplication.objects.filter(
        leave_type__requires_full_routing=True, status=LeaveApplication.SUBMITTED
    ).select_related("employee")
    supervisor_ids = [
        app.pk for app in supervisor_candidates if is_supervisor_of(acting_employee, app.employee)
    ]
    qs = qs | LeaveApplication.objects.filter(pk__in=supervisor_ids)

    return qs.distinct()
