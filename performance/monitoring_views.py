"""Performance Monitoring (HR), R&R feed, Performance Report and COSP renewal
averages (PMS step 7)."""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from . import monitoring as mon, rules
from .models import RatingWindow
from .views import _acting

SUPPLEMENTARY = "Supplementary: an input to the renewal decision, never its only basis. The AO and the COH decide."


def _report_reader(acting):
    return rules.is_hr(acting) or rules.is_ao(acting, True) or rules.is_coh(acting, True)


def _window(request):
    wid = request.GET.get("window")
    return get_object_or_404(RatingWindow, pk=wid) if wid else mon.window_choices().first()


@login_required
def monitoring(request):
    acting = _acting(request)
    if not rules.is_hr(acting):
        raise PermissionDenied("Performance Monitoring is for HR.")
    today = timezone.localdate()
    cosp = request.GET.get("scope") == "cosp"
    window = None if cosp else _window(request)
    if cosp:
        periods = mon.periods_for(cosp_from=today - timedelta(days=90), cosp_to=today + timedelta(days=90))
    elif window:
        periods = mon.periods_for(window)
    else:
        periods = mon.periods_for(cosp_from=today, cosp_to=today).none()
    periods = list(periods.prefetch_related("employee__units", "employee__sections"))
    return render(request, "performance/monitoring.html", {
        "windows": mon.window_choices(), "window": window, "cosp": cosp, "count": len(periods),
        "completion": mon.completion(periods), "load": mon.load(periods), "queues": mon.queues(periods),
        "averages": mon.unit_averages(periods), "min_n": mon.min_n()})


@login_required
def rr_feed(request):
    from dashboard.exports import _xlsx_response

    acting = _acting(request)
    if not rules.is_hr(acting):
        raise PermissionDenied("The R&R feed is for HR.")
    window = get_object_or_404(RatingWindow, pk=request.GET.get("window"))
    header, rows = mon.rr_feed(window)
    return _xlsx_response(f"rr_feed_{window.semester}", [("Final results", header, rows)])


@login_required
def report(request):
    from dashboard.exports import _pdf_response, _xlsx_response

    acting = _acting(request)
    if not _report_reader(acting):
        raise PermissionDenied("The Performance Report is for HR, the AO and the COH.")
    window = _window(request)
    s = mon.summary(window) if window else None
    fmt = request.GET.get("format")
    if s and fmt in ("xlsx", "pdf"):
        tables = mon.summary_tables(s)
        name = f"performance_summary_{window.semester}"
        if fmt == "xlsx":
            short = ["Overview", "By description", "By section", "Strongest items", "Common gaps"]
            return _xlsx_response(name, [(sh, hdr, rows) for sh, (h, hdr, rows) in zip(short, tables)])
        return _pdf_response(name, f"BDH HRIS &mdash; Performance Summary, {window.name}", tables)
    return render(request, "performance/report.html", {"windows": mon.window_choices(), "window": window, "s": s})


@login_required
def renewal(request):
    acting = _acting(request)
    if not _report_reader(acting):
        raise PermissionDenied("COSP renewal averages are for HR, the AO and the COH.")
    rows = [r for r in mon.renewal_rows() if r["contract"].employee_id != acting.pk]
    return render(request, "performance/renewal.html", {"rows": rows, "note": SUPPLEMENTARY})
