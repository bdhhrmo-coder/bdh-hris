"""
Same scope as the dashboard (dashboard/permissions.py): a hospital-wide
compliance/oversight view is for HR, AO, COH, and System Administrator —
not every Employee/Supervisor.
"""

from accounts.models import RoleAssignment
from leave.permissions import (  # noqa: F401 (re-exported for auditlog callers)
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
)


def can_view_audit_log(acting_employee):
    if acting_employee is None:
        return False
    return (
        is_hr(acting_employee)
        or is_administrative_officer(acting_employee)
        or is_chief_of_hospital(acting_employee)
        or acting_employee.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)
    )
