"""
Homepage KPI figures (Batch 4, owner decisions 2026-10-09). Every number is
read straight from HRIS records when the page loads; nothing is stored.
Biometric attendance is NOT used here.

Who sees what (enforced here, on the server):
  scope "all"      HR Processor / HR Administrator / AO / COH: whole hospital
  scope "sections" Supervisor (incl. OIC): employees of their own sections/units
  scope "self"     everyone else: numbers only for Absent/Pending, and their
                   OWN requests for Pending Applications
  Only HR sees the specific leave type; everyone else sees "Leave" or "CTO".

Absent Today = approved OR recorded leave (Regular leave is recorded, COSP
leave is approved) or approved CTO covering today. One row per person.
Official Business, Official Time and Travel are not absences.
"""

from collections import OrderedDict

from django.db.models import Q
from django.utils import timezone

from attendance.models import AttendanceCorrectionRequest
from cto.models import CTOUsageApplication
from employees.models import Employee
from exchange.models import DutyExchangeRequest
from leave.models import LeaveApplication
from leave.permissions import is_administrative_officer, is_chief_of_hospital, is_hr
from official_requests.models import OfficialRequest

LEAVE_DONE = [LeaveApplication.APPROVED, LeaveApplication.RECORDED]


def scope_for(acting_employee):
    if acting_employee is None:
        return "self"
    if is_hr(acting_employee) or is_administrative_officer(acting_employee) or is_chief_of_hospital(acting_employee):
        return "all"
    if acting_employee.has_role("SUPERVISOR"):
        return "sections"
    return "self"


def shows_leave_type(acting_employee):
    return acting_employee is not None and is_hr(acting_employee)


def scoped_employee_ids(acting_employee, scope):
    """None = no limit (whole hospital)."""
    if scope == "all":
        return None
    if scope == "sections":
        from official_requests.batch import supervised_employees

        return set(supervised_employees(acting_employee).values_list("pk", flat=True))
    return {acting_employee.pk} if acting_employee else set()


def _limit(qs, ids, field="employee_id"):
    return qs if ids is None else qs.filter(**{f"{field}__in": ids})


def headcount():
    return Employee.objects.filter(is_active=True, archived_at__isnull=True).count()


# -- absent today ------------------------------------------------------------------

def absent_today(today=None, ids=None, with_type=False):
    """One row per person: name, sections, what (Leave/CTO, or the leave type
    for HR), and the dates. Sorted by first section, then name."""
    today = today or timezone.localdate()
    rows = OrderedDict()
    leave = _limit(LeaveApplication.objects.filter(status__in=LEAVE_DONE, start_date__lte=today, end_date__gte=today),
                   ids).select_related("employee", "leave_type")
    cto = _limit(CTOUsageApplication.objects.filter(status=CTOUsageApplication.APPROVED, start_date__lte=today,
                                                    end_date__gte=today), ids).select_related("employee")
    for app in leave:
        label = app.leave_type.name if with_type else "Leave"
        rows.setdefault(app.employee_id, {"employee": app.employee, "what": [], "dates": []})
        rows[app.employee_id]["what"].append(label)
        rows[app.employee_id]["dates"].append((app.start_date, app.end_date))
    for app in cto:
        rows.setdefault(app.employee_id, {"employee": app.employee, "what": [], "dates": []})
        rows[app.employee_id]["what"].append("CTO")
        rows[app.employee_id]["dates"].append((app.start_date, app.end_date))
    return _sorted_by_section(rows.values())


def _sorted_by_section(rows):
    rows = list(rows)
    for r in rows:
        names = sorted(s.name for s in r["employee"].sections.all())
        r["section"] = ", ".join(names) or "—"
        r["sort"] = (names[0] if names else "~", r["employee"].surname.lower(), r["employee"].first_name.lower())
    return sorted(rows, key=lambda r: r["sort"])


# -- pending leave / CTO (beside Absent Today) --------------------------------------

def pending_leave_cto(ids=None, with_type=False):
    rows = []
    leave = _limit(LeaveApplication.objects.exclude(status__in=list(LeaveApplication.TERMINAL_STATUSES)
                                                    + [LeaveApplication.RETURNED]), ids)
    for app in leave.select_related("employee", "leave_type"):
        rows.append({"employee": app.employee, "what": [app.leave_type.name if with_type else "Leave"],
                     "dates": [(app.start_date, app.end_date)], "status": app.get_status_display()})
    cto = _limit(CTOUsageApplication.objects.exclude(status__in=list(CTOUsageApplication.TERMINAL_STATUSES)
                                                     + [CTOUsageApplication.RETURNED]), ids)
    for app in cto.select_related("employee"):
        rows.append({"employee": app.employee, "what": ["CTO"], "dates": [(app.start_date, app.end_date)],
                     "status": app.get_status_display()})
    return _sorted_by_section(rows)


# -- pending applications (all types) ------------------------------------------------

def _pending_querysets():
    """(label, queryset of requests waiting at some approval step, employee field)."""
    R = OfficialRequest
    return [
        ("Leave", LeaveApplication.objects.exclude(
            status__in=list(LeaveApplication.TERMINAL_STATUSES) + [LeaveApplication.RETURNED]), "employee_id"),
        ("CTO", CTOUsageApplication.objects.exclude(
            status__in=list(CTOUsageApplication.TERMINAL_STATUSES) + [CTOUsageApplication.RETURNED]), "employee_id"),
        ("Exchange of Duty", DutyExchangeRequest.objects.exclude(
            status__in=list(DutyExchangeRequest.DEAD_STATUSES) + [DutyExchangeRequest.APPROVED,
                                                                 DutyExchangeRequest.RETURNED]), "employee_a_id"),
        ("Attendance correction", AttendanceCorrectionRequest.objects.filter(
            status__in=AttendanceCorrectionRequest.OPEN_STATUSES - {AttendanceCorrectionRequest.RETURNED}),
         "employee_id"),
        ("Official Business/Time/Travel/OT", R.objects.exclude(
            status__in=list(R.TERMINAL_STATUSES) + [R.RETURNED]), "employee_id"),
    ]


def _in_scope(qs, field, ids):
    if ids is None:
        return qs
    cond = Q(**{f"{field}__in": ids})
    if field == "employee_a_id":  # an exchange concerns both partners
        cond |= Q(employee_b_id__in=ids)
    return qs.filter(cond).distinct()


def pending_applications(ids=None):
    """[(label, count)] of requests waiting at any approval step, within scope."""
    return [(label, _in_scope(qs, field, ids).count()) for label, qs, field in _pending_querysets()]


def pending_rows(ids=None):
    """Detailed list for the 'Pending applications' page."""
    rows = []
    for label, qs, field in _pending_querysets():
        for obj in _in_scope(qs, field, ids):
            emp = getattr(obj, "employee", None) or getattr(obj, "employee_a", None)
            filed = getattr(obj, "submitted_at", None) or getattr(obj, "filed_at", None)
            rows.append({"type": label, "employee": emp, "filed": filed, "status": obj.get_status_display()})
    return sorted(rows, key=lambda r: (r["filed"] is None, r["filed"]))


# -- waiting for MY action ----------------------------------------------------------

def waiting_for_me(acting_employee, user):
    """[(label, count, queue url name)] - reuses each module's own queue rules."""
    from attendance.permissions import visible_correction_requests_for
    from cto.permissions import visible_cto_applications_for
    from exchange.permissions import visible_exchange_requests_for
    from leave.permissions import visible_applications_for
    from official_requests.batch import batches_awaiting
    from official_requests.permissions import visible_requests_for

    if acting_employee is None:
        return []
    items = [
        ("Leave", visible_applications_for(acting_employee).count(), "leave:leave_queue"),
        ("CTO", visible_cto_applications_for(acting_employee).count(), "cto:cto_queue"),
        ("Exchange of Duty", visible_exchange_requests_for(acting_employee).count(), "exchange:exchange_queue"),
        ("Attendance correction", visible_correction_requests_for(acting_employee).count(),
         "attendance:correction_queue"),
        ("Official Business/Time/Travel/OT",
         visible_requests_for(acting_employee).count() + len(batches_awaiting(acting_employee, user)),
         "official_requests:request_queue"),
    ]
    return [i for i in items if i[1]]
