"""Performance Setup, Periods and the rating forms (PMS steps 2-3)."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from employees.models import Employee
from employees.permissions import get_acting_employee

from . import raters, rules
from .models import (ClinicalSection, EvaluationPeriod as P, PMSetting, RaterAssignment as A, RatingWindow,
                     WordingOverride)
from .seed_data import SETTING_LABELS, SETTINGS


def _acting(request):
    acting = get_acting_employee(request.user)
    if acting is None:
        raise PermissionDenied("No employee record is linked to your account.")
    return acting


def _date(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValidationError("Enter a valid date.")


def _run(request, fn, ok="Saved."):
    try:
        result = fn()
        if ok:
            messages.success(request, ok)
        return result
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))


SETUP_TABS = [("performance:setup", "Settings and forms"), ("performance:periods", "Periods"),
              ("performance:deduction_criteria", "Deduction Criteria")]


@login_required
def setup(request):
    acting = _acting(request)
    if not rules.is_hr(acting):
        raise PermissionDenied("Performance Setup is for HR.")
    if request.method == "POST":
        what = request.POST.get("what")
        if what == "setting":
            _run(request, lambda: rules.change_setting(
                acting, request.user, request.POST.get("key"), request.POST.get("value"),
                _date(request.POST.get("effective_from")), request.POST.get("approval_ref")),
                "Setting saved. It applies from its effective date.")
        elif what == "wording":  # v1.1 §3.1: the HR Processor assigns the wording (any HR person may)
            emp = get_object_or_404(Employee, pk=request.POST.get("employee"))
            track, reason = request.POST.get("track"), request.POST.get("reason", "").strip()
            if emp.pk == acting.pk:
                messages.error(request, "Not on your own record.")
            elif track == "":
                WordingOverride.objects.filter(employee=emp).delete()
                messages.success(request, "Back to the section default.")
            elif not reason:
                messages.error(request, "Type the reason.")
            else:
                WordingOverride.objects.update_or_create(employee=emp, defaults=dict(track=track, reason=reason,
                                                                                     set_by=request.user))
                messages.success(request, "Wording set.")
        return redirect("performance:setup")
    rows = []
    for key in SETTINGS:
        cur = rules.setting_row(key)
        future = PMSetting.objects.filter(key=key, effective_from__gt=timezone.localdate()).order_by("effective_from")
        rows.append({"key": key, "label": SETTING_LABELS.get(key, key), "current": cur, "future": list(future)})
    return render(request, "performance/setup.html", {
        "tabs": SETUP_TABS, "rows": rows, "is_admin": rules.is_hr_admin(acting), "today": timezone.localdate(),
        "clinical": ClinicalSection.objects.select_related("section"),
        "overrides": WordingOverride.objects.select_related("employee"),
        "employees": Employee.objects.filter(is_active=True, archived_at__isnull=True)})


@login_required
def periods(request):
    acting = _acting(request)
    if not rules.is_hr(acting):
        raise PermissionDenied("Performance Periods are for HR.")
    if request.method == "POST":
        what = request.POST.get("what")
        if what == "window":
            _run(request, lambda: rules.create_window(acting, request.user, request.POST.get("semester", "").strip(),
                                                      _date(request.POST.get("opens_on")),
                                                      _date(request.POST.get("closes_on"))), "Window created.")
        elif what in ("open", "close"):
            w = get_object_or_404(RatingWindow, pk=request.POST.get("pk"))
            if what == "open":
                warnings = _run(request, lambda: rules.open_window(acting, request.user, w), "Window opened.")
                for msg in warnings or []:
                    messages.warning(request, msg)
            else:
                _run(request, lambda: rules.close_window(acting, request.user, w), "Window closed.")
        elif what == "plan":
            from outonpass.models import CospContractPeriod

            c = get_object_or_404(CospContractPeriod, pk=request.POST.get("pk"))
            n = _run(request, lambda: rules.plan_cosp_periods(acting, request.user, c), None)
            if n is not None:
                messages.success(request, f"{n} period(s) planned.")
        return redirect("performance:periods")
    from outonpass.models import CospContractPeriod

    planned = set(P.objects.exclude(contract=None).values_list("contract_id", flat=True))
    contracts = [c for c in CospContractPeriod.objects.filter(voided_at__isnull=True,
                                                               end_date__gte=timezone.localdate())
                 .select_related("employee") if c.pk not in planned]
    qs = P.objects.select_related("employee", "window").exclude(status=P.CANCELLED)
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    return render(request, "performance/periods.html", {
        "tabs": SETUP_TABS, "windows": RatingWindow.objects.annotate(n=Count("periods")), "contracts": contracts,
        "periods": qs[:300], "statuses": P.STATUSES, "status": status, "is_admin": rules.is_hr_admin(acting)})


def sees_rater_names(acting, period):
    return rules.is_hr_admin(acting) and acting.pk != period.employee_id


@login_required
def period_detail(request, pk):
    acting = _acting(request)
    period = get_object_or_404(P.objects.select_related("employee", "window"), pk=pk)
    if not rules.is_hr(acting) or acting.pk == period.employee_id:
        raise PermissionDenied("HR only, never on your own period.")
    if request.method == "POST":
        what = request.POST.get("what")
        if what == "open":
            _run(request, lambda: rules.open_planned(acting, request.user, period), "Period opened; raters assigned.")
        elif what == "cancel":
            _run(request, lambda: rules.cancel_period(acting, request.user, period, request.POST.get("reason")),
                 "Period cancelled.")
        elif what == "add":
            emp = get_object_or_404(Employee, pk=request.POST.get("rater"))
            _run(request, lambda: raters.hr_add_rater(acting, request.user, period, emp, request.POST.get("group"),
                                                      request.POST.get("reason")), "Rater added.")
        elif what == "remove":
            a = get_object_or_404(A, pk=request.POST.get("assignment"), period=period)
            _run(request, lambda: raters.hr_remove_rater(acting, request.user, a, request.POST.get("reason")),
                 "Rater removed.")
        else:
            from . import results

            results.handle_period_post(request, acting, period)
        return redirect("performance:period", pk=pk)
    from . import results

    ctx = {"period": period, "names": sees_rater_names(acting, period), "is_admin": rules.is_hr_admin(acting),
           "assignments": period.raters.select_related("rater").order_by("group", "pk"),
           "counts": period.raters.values("group", "status").annotate(n=Count("pk")).order_by("group"),
           "groups": [g for g in A.GROUPS if g[0] != A.SELF],
           "opens_on": period.end_date - timedelta(days=int(rules.number_for("cosp_open_days_before_end", period.end_date))),
           "employees": Employee.objects.filter(is_active=True, archived_at__isnull=True).exclude(pk=period.employee_id)}
    ctx.update(results.period_context(acting, period))
    # v1.1 §7.5: viewing raw comments or flagged forms leaves an audit entry (once a day per person).
    if ctx["names"] and (ctx["flagged"].exists() or ctx["pending_comments"].exists()) and not period.events.filter(
            action="viewed_raw", user=request.user, at__date=timezone.localdate()).exists():
        rules.log(period, "viewed_raw", request.user)
    return render(request, "performance/period.html", ctx)


@login_required
def tasks(request):
    acting = _acting(request)
    mine = P.objects.filter(employee=acting, period_type=P.SEMESTRAL, status=P.OPEN, window__status="OPEN").first()
    if request.method == "POST":
        if request.POST.get("what") == "propose" and mine:
            people = list(Employee.objects.filter(pk__in=request.POST.getlist("people")))
            _run(request, lambda: raters.propose_peers(acting, mine, people), "Proposals saved.")
        elif request.POST.get("what") == "finalize":
            period = get_object_or_404(P, pk=request.POST.get("period"))
            people = list(Employee.objects.filter(pk__in=request.POST.getlist("people")))
            _run(request, lambda: raters.finalize_peers(acting, request.user, period, people), "Peers picked.")
        return redirect("performance:tasks")
    from . import results

    to_finalize = []
    for period in raters.peers_to_finalize(acting):
        to_finalize.append({"period": period,
                            "need": int(rules.num("peers_per_ratee", period)) - raters.counted_peers(period).count(),
                            "proposed": period.raters.filter(status=A.PROPOSED).select_related("rater"),
                            "candidates": Employee.objects.filter(is_active=True, archived_at__isnull=True,
                                                                  sections__in=period.employee.sections.all())
                            .exclude(pk__in=[period.employee_id, acting.pk]).distinct()})
    from .monitoring import form_minutes

    # Progress (v1.1 §7.4; gap 7): forms of periods still being rated.
    current = A.objects.filter(rater=acting, status__in=A.LIVE | {A.SUBMITTED},
                               period__status=P.OPEN).select_related("period")
    total, done = current.count(), current.filter(status=A.SUBMITTED).count()
    left = sum(form_minutes(x) for x in current if x.status in A.LIVE)
    return render(request, "performance/tasks.html", {
        "progress": {"total": total, "done": done, "left": round(left)} if total else None,
        "tasks": raters.tasks_for(acting), "mine": mine, "to_finalize": to_finalize,
        "proposed": mine.raters.filter(status=A.PROPOSED).select_related("rater") if mine else [],
        "colleagues": Employee.objects.filter(is_active=True, archived_at__isnull=True,
                                              sections__in=acting.sections.all()).exclude(pk=acting.pk).distinct()
        if mine else [],
        "limit": int(rules.num("peer_proposals", mine)) if mine else 0,
        "cosp_forms": results.cosp_forms_waiting(acting)})


@login_required
def rate(request, pk):
    acting = _acting(request)
    a = get_object_or_404(A.objects.select_related("period__employee", "period__window"), pk=pk, rater=acting)
    if request.method == "POST":
        what = request.POST.get("what")
        if what == "contact":
            _run(request, lambda: raters.answer_contact(a, request.POST.get("answer") == "yes", request.user), None)
        elif what == "conflict":
            _run(request, lambda: raters.answer_conflict(a, request.POST.get("answer") == "applies", request.user),
                 None)
        elif what == "orientation":
            raters.acknowledge(a)
        elif what in ("save", "submit"):
            ok = "Submitted. Thank you." if what == "submit" else "Saved. You can finish later."
            _run(request, lambda: raters.save_answers(a, request.POST, request.user, submit=what == "submit"), ok)
            if what == "submit" and a.status == A.SUBMITTED:
                return redirect("performance:tasks")
        return redirect("performance:rate", pk=pk)
    step = None
    if a.status not in A.LIVE:
        step = "closed"
    elif raters.asks_contact(a) and a.contact_ok is None:
        step = "contact"
    elif raters.asks_conflict(a) and not a.conflict_cleared:
        step = "conflict"
    elif raters.needs_orientation(a):
        step = "orientation"
    else:
        step = "form"
        if a.first_shown_at is None:
            a.first_shown_at = timezone.now()
            a.save(update_fields=["first_shown_at"])
    answers = {r.indicator.code: r for r in a.responses.select_related("indicator")}
    comments = {c.kind: c.raw_text for c in a.comments.all()}
    groups = []
    for comp, inds in raters.items_for(a):
        groups.append((comp, [(i, i.wording(a.wording) if a.form != "C" else i.wording_1, answers.get(i.code))
                              for i in inds]))
    return render(request, "performance/rate.html", {
        "a": a, "step": step, "groups": groups, "comments": comments,
        "question": raters.contact_question(a) if raters.asks_contact(a) else "",
        "scale": [(5, "Always or almost always"), (4, "Often"), (3, "Sometimes"), (2, "Rarely"), (1, "Never")]})


@login_required
def rate_autosave(request, pk):
    """Autosave of a rating form on every answer (v1.1 §7.4; gap 6). HTMX only;
    the form still has its Submit and Save buttons."""
    from django.http import HttpResponse, HttpResponseNotAllowed

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    acting = _acting(request)
    a = get_object_or_404(A, pk=pk, rater=acting)
    try:
        raters.save_answers(a, request.POST, request.user, submit=False)
    except ValidationError as exc:
        return HttpResponse(" ".join(exc.messages))
    return HttpResponse(f"Saved {timezone.localtime():%I:%M %p}.")
