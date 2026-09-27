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


def _has_active_role(acting_employee, role_code):
    if acting_employee is None:
        return False
    return acting_employee.has_role(role_code)


def is_hr(acting_employee):
    return _has_active_role(acting_employee, RoleAssignment.HR_PROCESSOR) or _has_active_role(
        acting_employee, RoleAssignment.HR_ADMINISTRATOR
    )


def is_administrative_officer(acting_employee):
    return _has_active_role(acting_employee, RoleAssignment.ADMINISTRATIVE_OFFICER)


def is_chief_of_hospital(acting_employee):
    return _has_active_role(acting_employee, RoleAssignment.CHIEF_OF_HOSPITAL)


def is_supervisor_of(acting_employee, target_employee):
    """
    True if acting_employee holds an active Supervisor (or OIC-as-Supervisor)
    RoleAssignment whose section/unit overlaps the target's assignments.
    """
    if acting_employee is None:
        return False
    target_section_ids = set(target_employee.sections.values_list("id", flat=True))
    target_unit_ids = set(target_employee.units.values_list("id", flat=True))
    for ra in acting_employee.active_role_assignments().filter(role=RoleAssignment.SUPERVISOR):
        if ra.section_id is None and ra.unit_id is None:
            return True  # hospital-wide supervisor assignment
        if ra.section_id and ra.section_id in target_section_ids:
            return True
        if ra.unit_id and ra.unit_id in target_unit_ids:
            return True
    return False


def can_submit_for(acting_employee, target_employee):
    """An employee may only submit a leave application for themselves."""
    return acting_employee is not None and acting_employee.pk == target_employee.pk


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
