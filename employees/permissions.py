"""
Permission helpers for the Employee screen.

Kept as plain functions (not a permissions/ACL framework) — there are only
a handful of rules here and they read more clearly as code than as config:

  1. Only HR Administrators and System Administrators may open the Employee
     edit screen at all.
  2. employee_id and is_active are System-Administrator-only fields.
  3. An HR Administrator viewing/editing their OWN record gets a read-only
     view — the edit must be made by another HR Administrator (closes the
     CLAUDE.md 6.4 self-approval bypass).
"""

from accounts.models import RoleAssignment


def get_acting_employee(user):
    """Return the Employee record for the logged-in user, or None."""
    return getattr(user, "employee", None)


def can_open_employee_edit_screen(acting_employee):
    if acting_employee is None:
        return False
    return acting_employee.has_role(
        RoleAssignment.HR_ADMINISTRATOR
    ) or acting_employee.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)


def is_self_record_locked(acting_employee, target_employee):
    """
    True when the acting employee is an HR Administrator editing their own
    record. Per confirmed project decision, this makes the record fully
    read-only to them; another HR Administrator must make the edit.
    """
    if acting_employee is None:
        return False
    return (
        acting_employee.pk == target_employee.pk
        and acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)
    )


SYSTEM_ADMIN_ONLY_FIELDS = {"employee_id", "is_active"}
