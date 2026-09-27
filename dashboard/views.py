from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from employees.permissions import get_acting_employee
from orgstructure.models import Section

from . import charts, exports, stats
from .period import resolve_period
from .permissions import can_view_dashboard


def _require_dashboard_access(request):
    acting_employee = get_acting_employee(request.user)
    if not can_view_dashboard(acting_employee):
        raise PermissionDenied("The dashboard is available to HR, AO, COH, and System Administrator roles.")
    return acting_employee


def _selected_section(request):
    section_id = request.GET.get("section")
    if not section_id:
        return None
    return Section.objects.filter(pk=section_id).first()


@login_required
def dashboard_home(request):
    _require_dashboard_access(request)

    section = _selected_section(request)
    start_date, end_date, period_label = resolve_period(request.GET)

    type_rows, totals = stats.application_summary(start_date, end_date, section=section)
    department_rows = stats.department_statistics(start_date, end_date)
    trend = stats.monthly_trend(end_date)

    context = {
        "headcount": stats.headcount(section=section),
        "on_leave_today": stats.on_leave_today(section=section),
        "on_cto_today": stats.on_cto_today(section=section),
        "type_rows": type_rows,
        "totals": totals,
        "department_rows": department_rows,
        "period_label": period_label,
        "pie_chart": charts.status_pie_chart(totals),
        "trend_chart": charts.monthly_trend_chart(trend),
        "sections": Section.objects.all().order_by("name"),
        "selected_section": section,
        "get_params": request.GET,
    }
    return render(request, "dashboard/dashboard_home.html", context)


@login_required
def leave_balance_report(request):
    _require_dashboard_access(request)

    section = _selected_section(request)
    leave_types, rows = stats.leave_balance_rows(section=section)

    fmt = request.GET.get("format")
    if fmt in ("xlsx", "pdf"):
        return exports.export_leave_balance_report(leave_types, rows, fmt)

    return render(
        request,
        "dashboard/leave_balance_report.html",
        {
            "leave_types": leave_types,
            "rows": rows,
            "sections": Section.objects.all().order_by("name"),
            "selected_section": section,
        },
    )


@login_required
def application_summary_report(request):
    _require_dashboard_access(request)

    section = _selected_section(request)
    start_date, end_date, period_label = resolve_period(request.GET)
    type_rows, totals = stats.application_summary(start_date, end_date, section=section)
    department_rows = stats.department_statistics(start_date, end_date)

    fmt = request.GET.get("format")
    if fmt in ("xlsx", "pdf"):
        return exports.export_application_summary_report(period_label, type_rows, totals, department_rows, fmt)

    return render(
        request,
        "dashboard/application_summary_report.html",
        {
            "type_rows": type_rows,
            "totals": totals,
            "department_rows": department_rows,
            "period_label": period_label,
            "sections": Section.objects.all().order_by("name"),
            "selected_section": section,
        },
    )
