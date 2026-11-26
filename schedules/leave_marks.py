"""
Pilot Item 6 (owner checklist 2026-11-24): leave and CTO on the duty schedule.

Display and a check only; nothing here changes a leave, CTO or schedule rule.
Every mark is read from the requests themselves each time, so an approved date
change moves the mark by itself.

  LV    approved leave (days taken: LeaveApplication.counted_q)
  LV?   leave still in the chain
  CTO   approved CTO
  CTO?  CTO still in the chain
CTO days that only cover part of a COSP leave are shown as that leave (LV).

The leave code (settings.SCHEDULE_LEAVE_CODES, default "LV") may be put in a
draft only on a day that has a leave or CTO request (pending or approved);
anywhere else the cell keeps its old value and the reason is listed.
A duty shift on an approved leave/CTO day is a warning only.
"""

from django.conf import settings

APPROVED_MARKS = {"LV", "CTO"}


def leave_codes():
    return {c.upper() for c in getattr(settings, "SCHEDULE_LEAVE_CODES", ("LV",))}


def marks(schedule, employee_ids=None):
    """{(employee_id, date): "LV" | "LV?" | "CTO" | "CTO?"} for the schedule's month."""
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
            put(a.employee_id, a.start_date, a.end_date, "LV")
        elif a.status not in (L.APPROVED, L.RECORDED):
            put(a.employee_id, a.start_date, a.end_date, "LV?")
    cto = U.standalone().filter(employee_id__in=employee_ids, start_date__lte=last, end_date__gte=first).exclude(
        status__in=[U.RETURNED, U.REJECTED, U.CANCELLED])
    for a in cto:
        put(a.employee_id, a.start_date, a.end_date, "CTO" if a.status == U.APPROVED else "CTO?")
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
