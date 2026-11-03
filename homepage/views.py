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


def _request_filter(request, acting, scope, ids):
    """(ids, only) for the PENDING-REQUEST lists (owner decisions 2026-11-03,
    dry-run item 4): everyone for the HR Administrator, AO and COH; for HR
    Processors the requests they are involved in; for Supervisors those plus
    every pending request of their own units/sections; their own for
    everyone else."""
    if scope == "self":
        return ids, None
    if kpis.sees_all_requests(acting):
        return None, None
    only = kpis.involved_requests(acting, request.user)
    if acting.has_role("SUPERVISOR", viewing=True):
        kpis.add_area_requests(only, kpis.scoped_employee_ids(acting, "sections"))
    return None, only


def _pending_lc_rows(request, acting, scope, ids, with_type):
    req_ids, only = _request_filter(request, acting, scope, ids)
    return kpis.pending_leave_cto(ids=req_ids, with_type=with_type, only=only)


@login_required
def home(request):
    acting, scope, ids, with_type = _context(request)
    absent_ids = None if scope == "self" else ids  # employees see the hospital-wide NUMBER only
    absent = kpis.absent_today(ids=absent_ids)
    req_ids, only = _request_filter(request, acting, scope, ids)
    pending_lc = kpis.pending_leave_cto(ids=None if scope == "self" else req_ids, only=only)
    pending_apps = kpis.pending_applications(ids=req_ids, only=only)
    ctx = {
        "scope": scope,
        "as_of": timezone.localtime(),
        "headcount": kpis.headcount(),
        "absent_count": len(absent),
        "pending_lc_count": len(pending_lc),
        "pending_apps": pending_apps,
        "pending_apps_total": sum(n for _, n in pending_apps),
        "pending_involved_only": scope != "self" and not kpis.sees_all_requests(acting),
        "waiting": kpis.waiting_for_me(acting, request.user),
        "watermark_url": watermark_url(),
    }
    ctx["waiting_total"] = sum(n for _, n, _ in ctx["waiting"])
    if scope != "self":  # names only for HR, AO, COH and Supervisors (CLAUDE.md 8B)
        ctx["month_leave"] = kpis.on_leave_this_month(ids=ids, with_type=with_type)
        ctx["month_cto"] = kpis.on_cto_this_month(ids=ids)
    ctx.update(_role_cards(acting, scope))
    try:
        from announcements.views import homepage_panel

        ctx.update(homepage_panel(request, acting))
    except ImportError:
        pass
    return render(request, "homepage/home.html", ctx)


def _role_cards(acting, scope):
    """Which Home cards this person gets (Batch 6, owner-approved arrangement,
    max 5, actions first). Only existing data; the KPI rules are unchanged.
      HR / AO / COH ("all"):  Waiting for my action, Absent today, Pending applications, Headcount
      Supervisor ("sections"): Waiting for my action, Absent today, Pending requests I handle
      Everyone else ("self"):  [Waiting - ICTU] / [User accounts - System Administrator],
                               My pending requests, Absent today (number), My balances, My schedule this week
    """
    from accounts.models import RoleAssignment
    from employees.context_processors import _APPROVAL_ROLES

    is_ictu = acting is not None and acting.has_role(RoleAssignment.ICTU_STAFF)
    can_act = acting is not None and (is_ictu or any(acting.has_role(r) for r in _APPROVAL_ROLES))
    personal = scope == "self"
    ctx = {"show_waiting": can_act, "show_headcount": scope == "all", "show_personal": personal,
           "show_accounts": acting is not None and acting.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)}
    if personal and acting is not None:
        from leave.balance_cards import balance_cards

        ctx["my_balances"] = balance_cards(acting)
        ctx["my_week"] = _my_week(acting)
    return ctx


def _my_week(acting):
    """The next 7 days from RECORDED duty schedules (planned shifts, not
    attendance). Empty list = no recorded schedule covers this week."""
    from datetime import timedelta

    from schedules.models import DutySchedule, ScheduleCell

    today = timezone.localdate()
    cells = (ScheduleCell.objects.filter(row__employee=acting, date__gte=today, date__lt=today + timedelta(days=7),
                                         row__schedule__status=DutySchedule.RECORDED)
             .select_related("shift").order_by("date"))
    return [(c.date, c.shift.code if c.shift_id else "—") for c in cells]


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
        "title": "Pending leave and CTO",
        "rows": _pending_lc_rows(request, acting, scope, ids, with_type),
        "scope": scope, "as_of": timezone.localtime(), "show_status": True,
        "note": "Filed but not yet approved or recorded. These are NOT counted as absent.",
    })


@login_required
def pending_applications_list(request):
    acting, scope, ids, with_type = _context(request)
    return render(request, "homepage/pending_list.html", {
        "rows": kpis.pending_rows(*_request_filter(request, acting, scope, ids)), "scope": scope,
        "as_of": timezone.localtime(), "involved_only": scope != "self" and not kpis.sees_all_requests(acting),
    })
