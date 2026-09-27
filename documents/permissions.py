"""
Access control for uploaded documents — CLAUDE.md §10: "document access
restricted by role and transaction authority... restrict accordingly, not
just behind general login."

Reimplements the same owner/HR/AO/COH/Supervisor visibility rule every
other app's can_view_* function already uses (rather than importing five
different apps' functions here, which would need per-model dispatch
anyway) — but a CONFIDENTIAL document narrows that down further per the
2026-09-27 decision: owner, HR, and System Administrator only. Supervisor/
AO/COH can see the surrounding transaction and that a document was
submitted (documents/requirements.py's status rows), just not open a
confidential file.
"""

from accounts.models import RoleAssignment
from leave.permissions import get_acting_employee, is_supervisor_of  # noqa: F401 (re-exported)


def _owner_employee_ids(obj):
    """Most transaction models have a single `employee`; DutyExchangeRequest
    has two (employee_a/employee_b), both of whom own the record."""
    if hasattr(obj, "employee_id"):
        return [obj.employee_id]
    ids = []
    if hasattr(obj, "employee_a_id"):
        ids.append(obj.employee_a_id)
    if hasattr(obj, "employee_b_id"):
        ids.append(obj.employee_b_id)
    return ids


def is_owner(acting_employee, obj):
    return acting_employee is not None and acting_employee.pk in _owner_employee_ids(obj)


def _is_hr(acting_employee):
    return acting_employee is not None and (
        acting_employee.has_role(RoleAssignment.HR_PROCESSOR) or acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)
    )


def _is_administrative_officer(acting_employee):
    return acting_employee is not None and acting_employee.has_role(RoleAssignment.ADMINISTRATIVE_OFFICER)


def _is_chief_of_hospital(acting_employee):
    return acting_employee is not None and acting_employee.has_role(RoleAssignment.CHIEF_OF_HOSPITAL)


def can_view_transaction(acting_employee, obj):
    """Whether acting_employee can see the underlying transaction at all —
    the same owner/HR/AO/COH/Supervisor-of-any-owner rule used everywhere
    else in this system."""
    if acting_employee is None:
        return False
    if is_owner(acting_employee, obj):
        return True
    if _is_hr(acting_employee) or _is_administrative_officer(acting_employee) or _is_chief_of_hospital(acting_employee):
        return True
    return any(is_supervisor_of(acting_employee, e) for e in _owned_employees(obj))


def _owned_employees(obj):
    employees = []
    for field in ("employee", "employee_a", "employee_b"):
        emp = getattr(obj, field, None)
        if emp is not None:
            employees.append(emp)
    return employees


def can_upload_to(acting_employee, obj):
    """Who may attach a document to this transaction: its owner(s), HR, or
    System Administrator — not Supervisor/AO/COH, who only review."""
    if acting_employee is None:
        return False
    if is_owner(acting_employee, obj):
        return True
    return _is_hr(acting_employee) or acting_employee.is_system_administrator()


def can_view_document(acting_employee, document):
    """The confidentiality gate: a confidential document is visible only
    to the owner, HR, and System Administrator, regardless of who could
    otherwise view the transaction it's attached to."""
    obj = document.content_object
    if obj is None:
        return False
    if document.is_confidential:
        return is_owner(acting_employee, obj) or _is_hr(acting_employee) or (
            acting_employee is not None and acting_employee.is_system_administrator()
        )
    return can_view_transaction(acting_employee, obj)
