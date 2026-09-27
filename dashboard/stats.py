"""
Aggregation for the dashboard and its two reports (CLAUDE.md §12).

Each of the five routed request types (leave, CTO, exchange, attendance
correction, official requests) has its own status choices, so rather than
inventing one shared status enum all five would have to adopt, this module
maps each model's own statuses into three dashboard-level buckets —
pending / approved / not approved — with one small function per type. An
"application" here is the request record itself, not the person: a duty
exchange counts once even though it touches two employees.
"""

from calendar import monthrange
from datetime import date

from django.db.models import Q

from attendance.models import AttendanceCorrectionRequest
from cto.models import CTOUsageApplication
from employees.models import Employee
from exchange.models import DutyExchangeRequest
from leave.models import LeaveApplication
from official_requests.models import OfficialRequest
from orgstructure.models import Section


def _bucket(queryset, approved_statuses, not_approved_statuses):
    total = queryset.count()
    approved = queryset.filter(status__in=approved_statuses).count()
    not_approved = queryset.filter(status__in=not_approved_statuses).count()
    return {"pending": total - approved - not_approved, "approved": approved, "not_approved": not_approved}


def leave_counts(start_date, end_date, section=None):
    qs = LeaveApplication.objects.filter(submitted_at__date__range=(start_date, end_date))
    if section:
        qs = qs.filter(employee__sections=section).distinct()
    return _bucket(
        qs,
        {LeaveApplication.APPROVED, LeaveApplication.RECORDED},
        {LeaveApplication.REJECTED, LeaveApplication.RETURNED, LeaveApplication.CANCELLED},
    )


def cto_counts(start_date, end_date, section=None):
    qs = CTOUsageApplication.objects.filter(submitted_at__date__range=(start_date, end_date))
    if section:
        qs = qs.filter(employee__sections=section).distinct()
    return _bucket(
        qs,
        {CTOUsageApplication.APPROVED},
        {CTOUsageApplication.REJECTED, CTOUsageApplication.RETURNED, CTOUsageApplication.CANCELLED},
    )


def exchange_counts(start_date, end_date, section=None):
    qs = DutyExchangeRequest.objects.filter(filed_at__date__range=(start_date, end_date))
    if section:
        qs = qs.filter(Q(employee_a__sections=section) | Q(employee_b__sections=section)).distinct()
    return _bucket(
        qs,
        {DutyExchangeRequest.APPROVED},
        {DutyExchangeRequest.REJECTED, DutyExchangeRequest.RETURNED, DutyExchangeRequest.CONSENT_DECLINED},
    )


def attendance_correction_counts(start_date, end_date, section=None):
    qs = AttendanceCorrectionRequest.objects.filter(submitted_at__date__range=(start_date, end_date))
    if section:
        qs = qs.filter(employee__sections=section).distinct()
    return _bucket(
        qs,
        {AttendanceCorrectionRequest.APPROVED},
        {AttendanceCorrectionRequest.REJECTED, AttendanceCorrectionRequest.RETURNED},
    )


def official_request_counts(start_date, end_date, section=None):
    qs = OfficialRequest.objects.filter(submitted_at__date__range=(start_date, end_date))
    if section:
        qs = qs.filter(employee__sections=section).distinct()
    return _bucket(
        qs,
        {OfficialRequest.APPROVED},
        {OfficialRequest.REJECTED, OfficialRequest.RETURNED, OfficialRequest.CANCELLED},
    )


APPLICATION_TYPES = [
    ("Leave", leave_counts),
    ("CTO Usage", cto_counts),
    ("Duty Exchange", exchange_counts),
    ("Attendance Correction", attendance_correction_counts),
    ("Official Request", official_request_counts),
]


def application_summary(start_date, end_date, section=None):
    """Per-type pending/approved/not-approved breakdown, plus a combined
    total — feeds the dashboard tiles, the status pie chart, and the
    Application Summary report/export."""
    rows = []
    totals = {"pending": 0, "approved": 0, "not_approved": 0}
    for label, fn in APPLICATION_TYPES:
        counts = fn(start_date, end_date, section)
        rows.append({"label": label, **counts})
        for key in totals:
            totals[key] += counts[key]
    return rows, totals


def headcount(section=None):
    qs = Employee.objects.filter(is_active=True)
    if section:
        qs = qs.filter(sections=section)
    return qs.distinct().count()


def on_leave_today(section=None, today=None):
    today = today or date.today()
    qs = LeaveApplication.objects.filter(
        status__in=[LeaveApplication.APPROVED, LeaveApplication.RECORDED],
        start_date__lte=today,
        end_date__gte=today,
    )
    if section:
        qs = qs.filter(employee__sections=section)
    return qs.values("employee").distinct().count()


def on_cto_today(section=None, today=None):
    today = today or date.today()
    qs = CTOUsageApplication.objects.filter(
        status=CTOUsageApplication.APPROVED, start_date__lte=today, end_date__gte=today
    )
    if section:
        qs = qs.filter(employee__sections=section)
    return qs.values("employee").distinct().count()


def department_statistics(start_date, end_date):
    """One row per Section: headcount plus the application-status breakdown
    for the period, filed by employees in that section — the department
    statistics table/report."""
    rows = []
    for section in Section.objects.all().order_by("name"):
        _, totals = application_summary(start_date, end_date, section=section)
        rows.append({"section": section, "headcount": headcount(section=section), **totals})
    return rows


def monthly_trend(end_date, months=12, section=None):
    """Combined application volume (all five types, filed regardless of
    outcome) for each of the trailing `months` months ending at
    end_date's month — the trend graph."""
    points = []
    year, month = end_date.year, end_date.month
    for offset in range(months - 1, -1, -1):
        m = month - offset
        y = year
        while m <= 0:
            m += 12
            y -= 1
        points.append((y, m))

    results = []
    for y, m in points:
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
        total = 0
        for _, fn in APPLICATION_TYPES:
            counts = fn(start, end, section)
            total += counts["pending"] + counts["approved"] + counts["not_approved"]
        results.append({"label": start.strftime("%b %Y"), "count": total})
    return results


def leave_balance_rows(section=None):
    """One row per active employee, one column per tracked leave type
    (ACCRUED or ANNUAL_CAP — UNTRACKED types have no numeric balance to
    show), for the Leave Balance report/export. A leave type not
    applicable to an employee's employment status shows as None (blank in
    the report) rather than a misleading 0.00."""
    from leave.balances import compute_available_balance
    from leave.models import LeaveType

    employees_qs = Employee.objects.filter(is_active=True)
    if section:
        employees_qs = employees_qs.filter(sections=section)

    leave_types = list(
        LeaveType.objects.filter(is_active=True)
        .exclude(balance_tracking=LeaveType.TRACKING_UNTRACKED)
        .order_by("name")
    )

    rows = []
    for employee in employees_qs.distinct().order_by("surname", "first_name"):
        balances = []
        for lt in leave_types:
            if lt.applicable_to in (LeaveType.APPLICABLE_BOTH, employee.employment_status):
                balances.append(compute_available_balance(employee, lt))
            else:
                balances.append(None)
        rows.append({"employee": employee, "balances": balances})
    return leave_types, rows
