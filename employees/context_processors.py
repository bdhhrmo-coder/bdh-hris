from accounts.models import RoleAssignment

from .permissions import can_list_employees as _can_list_employees
from .permissions import get_acting_employee

# Roles that appear as an approval/endorsement step in any routing chain in
# CLAUDE.md §6.2 (Supervisor, HR Processor, HR Administrator, AO, COH).
# System Administrator is deliberately excluded here - CLAUDE.md §3 says it
# "cannot [do] routine HR transaction processing/approval", so it shouldn't
# see approval queues just by virtue of the role.
_APPROVAL_ROLES = {
    RoleAssignment.SUPERVISOR,
    RoleAssignment.HR_PROCESSOR,
    RoleAssignment.HR_ADMINISTRATOR,
    RoleAssignment.ADMINISTRATIVE_OFFICER,
    RoleAssignment.CHIEF_OF_HOSPITAL,
}


def nav_links(request):
    """
    Exposes the flags base.html's sidebar needs to show (or hide) the
    request-workflow "Queue" links, without every view passing them in.
    Mirrors the existing dashboard_link / audit_log_link pattern.

    Deliberately one coarse `has_approval_role` flag rather than a separate
    flag per module (is_leave_approver, is_cto_approver, ...): every
    approval-capable role sits in at least one routing chain in CLAUDE.md
    §6.2, and each queue view already self-filters to only what that
    specific employee may act on (leave/permissions.py
    visible_applications_for and its siblings in cto/exchange/attendance/
    official_requests) - so a queue link that turns out to be empty for a
    given role is expected, not a bug, and not worth a second permission
    check just to hide it.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    acting_employee = get_acting_employee(user)
    has_approval_role = acting_employee is not None and any(
        acting_employee.has_role(role) for role in _APPROVAL_ROLES
    )

    return {
        "can_list_employees": _can_list_employees(acting_employee),
        "has_approval_role": has_approval_role,
    }
