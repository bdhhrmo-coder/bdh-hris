"""Performance Incidents and Deduction Criteria (PMS step 5)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from employees.models import Employee

from . import deductions as dd, rules
from .models import Deduction, DeductionCriterion
from .views import SETUP_TABS, _acting, _date, _run


@login_required
def criteria(request):
    acting = _acting(request)
    if not rules.is_hr(acting):
        raise PermissionDenied("Deduction Criteria are kept by HR.")
    if request.method == "POST":
        c = get_object_or_404(DeductionCriterion, pk=request.POST.get("pk"))
        _run(request, lambda: dd.change_criterion(acting, request.user, c, request.POST.get("points_options", ""),
                                                  request.POST.get("cap", ""), request.POST.get("is_active") == "1",
                                                  request.POST.get("approval_ref")))
        return redirect("performance:deduction_criteria")
    return render(request, "performance/criteria.html", {"tabs": SETUP_TABS, "rows": DeductionCriterion.objects.all(),
                                                         "is_admin": rules.is_hr_admin(acting)})


def can_view(acting, d):
    if acting.pk in (d.employee_id, d.filed_by_id):
        return True
    return rules.is_hr(acting) or rules.is_ao(acting, True) or rules.is_coh(acting, True)


@login_required
def incidents(request):
    dd.finalize_due()
    acting = _acting(request)
    if request.method == "POST":
        emp = get_object_or_404(Employee, pk=request.POST.get("employee"))
        crit = get_object_or_404(DeductionCriterion, pk=request.POST.get("criterion"))
        d = _run(request, lambda: dd.file(acting, request.user, emp, crit, _date(request.POST.get("incident_date")),
                                          request.POST.get("description"), request.POST.get("points"),
                                          request.POST.get("evidence_ref"), request.POST.get("final_ref")),
                 "Filed. HR validates it next.")
        return redirect("performance:incident", pk=d.pk) if d else redirect("performance:incidents")
    return render(request, "performance/incidents.html", {
        "about_me": Deduction.objects.filter(employee=acting).select_related("criterion", "filed_by"),
        "filed": Deduction.objects.filter(filed_by=acting).select_related("criterion", "employee"),
        "waiting": dd.waiting_for(acting), "criteria": DeductionCriterion.objects.filter(is_active=True)
        .exclude(points_options=""), "employees": Employee.objects.filter(is_active=True, archived_at__isnull=True)
        .exclude(pk=acting.pk), "today": timezone.localdate(),
        "register": rules.is_hr(acting) or rules.is_ao(acting, True) or rules.is_coh(acting, True)})


@login_required
def incident(request, pk):
    acting = _acting(request)
    d = get_object_or_404(Deduction.objects.select_related("employee", "criterion", "filed_by"), pk=pk)
    if not can_view(acting, d):
        raise PermissionDenied("You cannot see this incident.")
    if request.method == "POST":
        what, note = request.POST.get("what"), request.POST.get("note", "")
        actions = {
            "validate": lambda: dd.validate(acting, request.user, d, True, note, request.POST.get("points") or None),
            "dismiss_hr": lambda: dd.validate(acting, request.user, d, False, note),
            "explain": lambda: dd.explain(acting, request.user, d, request.POST.get("text")),
            "approve": lambda: dd.decide(acting, request.user, d, True, note),
            "dismiss": lambda: dd.decide(acting, request.user, d, False, note),
            "review": lambda: dd.request_review(acting, request.user, d, request.POST.get("text")),
            "review_step": lambda: dd.review_step(acting, request.user, d, note, True),
            "review_dismiss": lambda: dd.review_step(acting, request.user, d, note, False),
        }
        if what in actions:
            _run(request, actions[what], "Recorded.")
        return redirect("performance:incident", pk=pk)
    return render(request, "performance/incident.html", {
        "d": d, "events": d.events.select_related("user__employee"), "is_employee": acting.pk == d.employee_id,
        "can_hr": rules.is_hr(acting) and acting.pk not in (d.employee_id, d.filed_by_id),
        "can_decide": dd.decider_ok(acting, d), "today": timezone.localdate(),
        "can_review": (d, "Review (HR)") in dd.waiting_for(acting) or any(x[0].pk == d.pk for x in dd.waiting_for(acting))})


@login_required
def register(request):
    acting = _acting(request)
    if not (rules.is_hr(acting) or rules.is_ao(acting, True) or rules.is_coh(acting, True)):
        raise PermissionDenied("The register is for HR, the AO and the COH.")
    qs = Deduction.objects.select_related("employee", "criterion", "filed_by").exclude(employee=acting)
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    return render(request, "performance/register.html", {"rows": qs, "statuses": Deduction.STATUSES, "status": status})
