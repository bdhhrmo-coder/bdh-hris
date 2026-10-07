"""
Archive (soft delete) of an employee record - Batch 3 Item 5, owner
decisions 2026-10-07:
  - HR Administrator only, never their own record, reason required.
  - Nothing is deleted. The record is hidden from the Employee list (a
    "Show archived" filter brings it back), from pickers and from dashboard
    counts (is_active is set off), and the person can't log in
    (accounts.backends refuses archived employees).
  - Undo (8-second snackbar) or Restore later puts it back exactly as it
    was, including the earlier Active/Inactive status.
  - Both directions are written to the record's edit history, which the
    Audit Log already shows (who, when, reason).
"""

from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment

from .models import EmployeeEditHistory

UNDO_SECONDS = 8
FIELD = "archived"
LABEL = "Archive status"


def can_archive(acting_employee, target):
    return (
        acting_employee is not None
        and acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)
        and acting_employee.pk != target.pk
    )


@transaction.atomic
def archive(employee, user, reason):
    employee.active_before_archive = employee.is_active
    employee.is_active = False
    employee.archived_at = timezone.now()
    employee.archived_by = user
    employee.archive_reason = reason[:255]
    employee.save()
    EmployeeEditHistory.objects.create(employee=employee, field_name=FIELD, field_label=LABEL,
                                       old_value="Not archived", new_value="Archived", reason=reason, changed_by=user)


@transaction.atomic
def restore(employee, user, undo=False):
    employee.is_active = True if employee.active_before_archive is None else employee.active_before_archive
    employee.active_before_archive = None
    employee.archived_at = None
    employee.archived_by = None
    employee.archive_reason = ""
    employee.save()
    EmployeeEditHistory.objects.create(
        employee=employee, field_name=FIELD, field_label=LABEL, old_value="Archived", new_value="Not archived",
        reason="Undo right after archiving" if undo else "Restored by HR Administrator", changed_by=user)
