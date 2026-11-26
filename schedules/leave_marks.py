"""
Pilot Item 6 (owner checklist 2026-11-24, revised by the owner 2026-11-25): leave
and CTO on the duty schedule.

Display and a check only; nothing here changes a leave, CTO or schedule rule.
Every mark is read from the requests themselves each time, so an approved date
change moves the mark by itself.

Shown (grid, edit screen, F50 printout):
  LV    approved leave (days taken: LeaveApplication.counted_q)
  CTO   approved CTO, or CTO still in the chain
Leave still in the chain shows nothing. There is no "?" mark anywhere.
CTO days that only cover part of a COSP leave are shown as that leave (LV).

The leave codes (settings.SCHEDULE_LEAVE_CODES = (leave code, CTO code), default "LV" and
"CTO") may be put in a schedule only on a day with a MATCHING request, pending or approved:
LV on a leave day, CTO on a CTO day (owner Decision 10, 2026-11-26). Anywhere else the
cell keeps its old value and the reason is listed.
A duty shift on a day of APPROVED leave or CTO is a warning only.
"""

from django.conf import settings

# Internal kinds -> what is shown ("" = nothing shown)
LV, LV_PENDING, CTO, CTO_PENDING = "LV", "LV_PENDING", "CTO", "CTO_PENDING"
APPROVED_MARKS = {LV, CTO}
SHOWN = {LV: "LV", LV_PENDING: "", CTO: "CTO", CTO_PENDING: "CTO"}


def leave_codes():
    return {c.upper() for c in getattr(settings, "SCHEDULE_LEAVE_CODES", ("LV", "CTO"))}


def code_kinds():
    """{"LV": (LV, LV_PENDING), "CTO": (CTO, CTO_PENDING)} from settings (leave code first)."""
    leave_code, cto_code = (c.upper() for c in getattr(settings, "SCHEDULE_LEAVE_CODES", ("LV", "CTO")))
    return {leave_code: (LV, LV_PENDING), cto_code: (CTO, CTO_PENDING)}


def code_error(code, mark):
    """Why `code` may not go on a day with `mark` (None when it may)."""
    kinds = code_kinds().get(code.upper())
    if kinds is None or mark in kinds:
        return None
    if mark is None:
        return f"{code} needs a {'leave' if LV in kinds else 'CTO'} request for that day"
    other = "leave" if mark in (LV, LV_PENDING) else "CTO"
    return f"{code} is for {'leave' if LV in kinds else 'CTO'} days; that day has a {other} request"


def shown(mark_map):
    """{key: "LV" | "CTO"} for display; pending leave is left out."""
    return {k: SHOWN[v] for k, v in mark_map.items() if SHOWN[v]}


def marks(schedule, employee_ids=None):
    """{(employee_id, date): LV | LV_PENDING | CTO | CTO_PENDING} for the schedule's month (internal kinds)."""
    from cto.models import CTOUsageApplication as U
    from leave.models import LeaveApplication as L

    days = schedule.days
    first, last = days[0], days[-1]
    if employee_ids is None:
        employee_ids = list(schedule.rows.values_list("employee_id", flat=True))
    out = {}

    def put(emp_id, start, end, mark):
        d = max(start, first)
        while d <= min(end, last):
            current = out.get((emp_id, d))
            if current is None or (mark in APPROVED_MARKS and current not in APPROVED_MARKS):
                out[(emp_id, d)] = mark  # an approved mark wins over a pending one
            d = d.fromordinal(d.toordinal() + 1)

    leave = L.objects.filter(employee_id__in=employee_ids, start_date__isnull=False, end_date__isnull=False,
                             start_date__lte=last, end_date__gte=first)
    counted = set(leave.filter(L.counted_q()).values_list("pk", flat=True))
    for a in leave.exclude(status__in=[L.RETURNED, L.REJECTED, L.CANCELLED]):
        if a.pk in counted:
            put(a.employee_id, a.start_date, a.end_date, LV)
        elif a.status not in (L.APPROVED, L.RECORDED):
            put(a.employee_id, a.start_date, a.end_date, LV_PENDING)
    cto = U.standalone().filter(employee_id__in=employee_ids, start_date__lte=last, end_date__gte=first).exclude(
        status__in=[U.RETURNED, U.REJECTED, U.CANCELLED])
    for a in cto:
        put(a.employee_id, a.start_date, a.end_date, CTO if a.status == U.APPROVED else CTO_PENDING)
    return out


def duty_on_leave(schedule, mark_map=None):
    """Warnings: a duty shift on a day of approved leave or CTO. [(employee, date, code, mark)]"""
    from .models import ScheduleCell

    mark_map = marks(schedule) if mark_map is None else mark_map
    out = []
    for c in (ScheduleCell.objects.filter(row__schedule=schedule, shift__is_duty=True)
              .select_related("row__employee", "shift").order_by("row__employee__surname", "date")):
        m = mark_map.get((c.row.employee_id, c.date))
        if m in APPROVED_MARKS:
            out.append((c.row.employee, c.date, c.shift.code, m))
    return out
