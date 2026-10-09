"""
Normalizes the seven existing accountability records (see models.py's
docstring) into one row shape for the audit log view, the same way
dashboard/stats.py normalizes five apps' differing status vocabularies
into one shared bucket scheme — no new storage, just a read-time merge.

Row shape (a plain dict, not a model — nothing here is ever saved):
    {
        "timestamp": datetime,
        "module": str,           # e.g. "Leave", "CTO", "Employee Record"
        "action": str,           # e.g. "SUBMITTED", "Edited Surname"
        "detail": str,           # human-readable one-liner
        "actor": User or None,   # who did it
        "employee_ids": {int},   # employee(s) the record concerns, for filtering
        "employee_display": str, # human-readable name(s) for the column
    }
"""

from cto.models import CTOUsageApplicationAction
from documents.models import UploadedDocumentEvent
from employees.models import EmployeeEditHistory
from exchange.models import DutyExchangeRequestAction
from attendance.models import AttendanceCorrectionRequestAction
from leave.models import LeaveApplicationAction
from official_requests.models import OfficialRequestAction


def _employees_for_object(obj):
    """Best-effort list of Employee objects a transaction record concerns."""
    if obj is None:
        return []
    if hasattr(obj, "employee_a_id"):  # DutyExchangeRequest
        return [e for e in (obj.employee_a, obj.employee_b) if e is not None]
    if hasattr(obj, "employee"):
        return [obj.employee] if obj.employee_id else []
    return []


def _employee_display(employees):
    return ", ".join(str(e) for e in employees) or "—"


def _rows_from_employee_edit_history(qs):
    rows = []
    for row in qs.select_related("employee", "changed_by"):
        rows.append(
            {
                "timestamp": row.edited_at,
                "module": "Employee Record",
                "action": f"Edited {row.field_label or row.field_name}",
                "detail": f"“{row.old_value}” → “{row.new_value}” — reason: {row.reason}",
                "actor": row.changed_by,
                "employee_ids": {row.employee_id},
                "employee_display": str(row.employee),
            }
        )
    return rows


def _rows_from_request_actions(qs, module_label, request_field):
    rows = []
    for row in qs.select_related(request_field, "acted_by"):
        request_obj = getattr(row, request_field)
        employees = _employees_for_object(request_obj)
        rows.append(
            {
                "timestamp": row.acted_at,
                "module": module_label,
                "action": row.action,
                "detail": f"{request_obj} — {row.notes}" if row.notes else str(request_obj),
                "actor": row.acted_by,
                "employee_ids": {e.pk for e in employees},
                "employee_display": _employee_display(employees),
            }
        )
    return rows


def _rows_from_document_events(qs):
    rows = []
    for row in qs.select_related("document", "document__content_type", "acted_by"):
        content_object = row.document.content_object
        employees = _employees_for_object(content_object)
        rows.append(
            {
                "timestamp": row.acted_at,
                "module": "Documents",
                "action": row.get_event_type_display(),
                "detail": f"{row.document.original_filename} ({row.document.content_type.model} #{row.document.object_id})"
                + (f" — {row.notes}" if row.notes else ""),
                "actor": row.acted_by,
                "employee_ids": {e.pk for e in employees},
                "employee_display": _employee_display(employees),
            }
        )
    return rows


def _rows_from_announcement_actions():
    """Batch 4: announcements are hospital-wide notices, not about one
    employee, so they have no employee filter value."""
    from announcements.models import AnnouncementAction

    rows = []
    for row in AnnouncementAction.objects.select_related("announcement", "acted_by"):
        rows.append({
            "timestamp": row.acted_at, "module": "Announcements", "action": row.action,
            "detail": f"{row.announcement}" + (f" — {row.notes}" if row.notes else ""),
            "actor": row.acted_by, "employee_ids": set(), "employee_display": "—",
        })
    return rows


def audit_rows(employee_id=None, module=None, date_from=None, date_to=None):
    """
    Merged, filtered, newest-first list of audit rows across all seven
    sources. Filtering happens in Python after each source applies its own
    date filter at the DB level, since the sources have no common table to
    join on.
    """
    rows = []
    rows += _rows_from_employee_edit_history(EmployeeEditHistory.objects.all())
    rows += _rows_from_request_actions(LeaveApplicationAction.objects.all(), "Leave", "application")
    rows += _rows_from_request_actions(CTOUsageApplicationAction.objects.all(), "CTO", "application")
    rows += _rows_from_request_actions(DutyExchangeRequestAction.objects.all(), "Exchange of Duty", "request")
    rows += _rows_from_request_actions(
        AttendanceCorrectionRequestAction.objects.all(), "Attendance Correction", "request"
    )
    rows += _rows_from_request_actions(OfficialRequestAction.objects.all(), "Official Request", "request")
    rows += _rows_from_document_events(UploadedDocumentEvent.objects.all())
    rows += _rows_from_announcement_actions()

    if employee_id:
        rows = [r for r in rows if employee_id in r["employee_ids"]]
    if module:
        rows = [r for r in rows if r["module"] == module]
    if date_from:
        rows = [r for r in rows if r["timestamp"].date() >= date_from]
    if date_to:
        rows = [r for r in rows if r["timestamp"].date() <= date_to]

    rows.sort(key=lambda r: r["timestamp"], reverse=True)
    return rows


MODULE_CHOICES = [
    "Employee Record",
    "Leave",
    "CTO",
    "Exchange of Duty",
    "Attendance Correction",
    "Official Request",
    "Documents",
    "Announcements",
]
