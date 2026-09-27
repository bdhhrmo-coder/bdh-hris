"""
CLAUDE.md §12: the dashboard is a management-reporting view — headcount,
leave balances, department statistics — so it's scoped the same way the
Employee master-data screen is (employees/permissions.py): HR, AO, COH, and
System Administrator only, not every Employee/Supervisor. Reuses the
existing role checks rather than a new permission scheme.
"""

from accounts.models import RoleAssignment
from leave.permissions import (  # noqa: F401 (re-exported for dashboard callers)
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
)


def can_view_dashboard(acting_employee):
    if acting_employee is None:
        return False
    return (
        is_hr(acting_employee)
        or is_administrative_officer(acting_employee)
        or is_chief_of_hospital(acting_employee)
        or acting_employee.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)
    )
