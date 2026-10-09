from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from employees.permissions import get_acting_employee

from . import kpis


def watermark_url():
    """The homepage watermark (owner decision 2026-10-09: homepage only).
    Blank setting or a missing file = no watermark."""
    from django.templatetags.static import static

    path = getattr(settings, "HOME_WATERMARK_IMAGE", "")
    return static(path) if path and finders.find(path) else ""


def _context(request):
    acting = get_acting_employee(request.user)
    scope = kpis.scope_for(acting)
    return acting, scope, kpis.scoped_employee_ids(acting, scope), kpis.shows_leave_type(acting)


@login_required
def home(request):
    acting, scope, ids, with_type = _context(request)
    absent_ids = None if scope == "self" else ids  # employees see the hospital-wide NUMBER only
    absent = kpis.absent_today(ids=absent_ids)
    pending_lc = kpis.pending_leave_cto(ids=absent_ids)
    pending_apps = kpis.pending_applications(ids=ids)
    ctx = {
        "scope": scope,
        "as_of": timezone.localtime(),
        "headcount": kpis.headcount(),
        "absent_count": len(absent),
        "pending_lc_count": len(pending_lc),
        "pending_apps": pending_apps,
        "pending_apps_total": sum(n for _, n in pending_apps),
        "waiting": kpis.waiting_for_me(acting, request.user),
        "watermark_url": watermark_url(),
    }
    ctx["waiting_total"] = sum(n for _, n, _ in ctx["waiting"])
    try:
        from announcements.views import homepage_panel

        ctx.update(homepage_panel(request, acting))
    except ImportError:
        pass
    return render(request, "homepage/home.html", ctx)


def _names_allowed(scope):
    if scope == "self":
        raise PermissionDenied("Only HR, the AO, the COH and Supervisors can see who is absent or pending.")


@login_required
def absent_list(request):
    acting, scope, ids, with_type = _context(request)
    _names_allowed(scope)
    return render(request, "homepage/person_list.html", {
        "title": "Absent today", "rows": kpis.absent_today(ids=ids, with_type=with_type), "scope": scope,
        "as_of": timezone.localtime(), "show_status": False,
        "note": "Approved or recorded leave, or approved CTO, covering today. Official Business, Official Time "
                "and Travel are not absences.",
    })


@login_required
def pending_leave_list(request):
    acting, scope, ids, with_type = _context(request)
    _names_allowed(scope)
    return render(request, "homepage/person_list.html", {
        "title": "Pending leave and CTO", "rows": kpis.pending_leave_cto(ids=ids, with_type=with_type),
        "scope": scope, "as_of": timezone.localtime(), "show_status": True,
        "note": "Filed but not yet approved or recorded. These are NOT counted as absent.",
    })


@login_required
def pending_applications_list(request):
    acting, scope, ids, with_type = _context(request)
    return render(request, "homepage/pending_list.html", {
        "rows": kpis.pending_rows(ids=ids), "scope": scope, "as_of": timezone.localtime(),
    })
