"""
Single write path for creating notifications, so every app calls one
function instead of constructing Notification rows itself.
"""

from .models import Notification


def notify(employees, message, url=""):
    """
    Create one Notification per recipient.

    `employees` may be a single Employee, None, or any iterable of
    Employee/None (callers often build a list like
    [request.employee_a, request.employee_b] without checking for
    duplicates or Nones first). None entries are skipped, and the same
    employee is only notified once even if they appear twice (e.g. someone
    who is both a Supervisor of the applicant and, incidentally, also an
    HR Processor would otherwise get the same notification from two
    different call sites in a single status change).
    """
    from employees.models import Employee

    if employees is None:
        return
    if isinstance(employees, Employee):
        employees = [employees]

    seen_ids = set()
    to_create = []
    for employee in employees:
        if employee is None or employee.pk in seen_ids:
            continue
        seen_ids.add(employee.pk)
        to_create.append(Notification(recipient=employee, message=message, url=url))

    if to_create:
        Notification.objects.bulk_create(to_create)
