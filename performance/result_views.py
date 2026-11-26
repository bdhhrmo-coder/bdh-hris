"""My Performance Results, the COSP Performance Form, and Performance Reviews
and Contests (PMS step 4)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import deductions, raters, results, rules, scoring
from .models import Contest, EvaluationPeriod as P, RaterAssignment as A
from .views import _acting, _date, _run

SHOWN = [P.RELEASED, P.DISPUTED, P.FINAL]


def can_view_result(acting, period):
    if acting.pk == period.employee_id:
        return period.status in SHOWN
    if results.is_conference_supervisor(acting, period):
        return period.status in SHOWN
    if rules.is_hr(acting):
        return True
    return (rules.is_ao(acting) or rules.is_coh(acting)) and period.contests.exists()


@login_required
def my_results(request):
    acting = _acting(request)
    results.finalize_due()
    mine = P.objects.filter(employee=acting, status__in=SHOWN)
    from datetime import timedelta

    from . import monitoring

    # Team view (v1.1 §7.4; gap 12): every current or recent period of my staff, with where it stands.
    since = timezone.localdate() - timedelta(days=400)
    team = []
    for p in (P.objects.exclude(status__in=[P.PLANNED, P.CANCELLED]).filter(end_date__gte=since)
              .select_related("employee").order_by("employee__surname", "-end_date")):
        if not results.is_conference_supervisor(acting, p):
            continue
        my_form = p.raters.filter(rater=acting, group=A.SUPERVISOR).first()
        self_form = p.raters.filter(group=A.SELF).first()
        team.append({"p": p, "shown": p.status in SHOWN, "my_form": my_form, "self_form": self_form,
                     "plan": getattr(p, "dev_plan", None)})
    return render(request, "performance/my_results.html", {
        "mine": mine, "team": team, "show": scoring.show, "team_average": monitoring.team_average(acting),
        "given": monitoring.my_given(acting)})


@login_required
def result_detail(request, pk):
    acting = _acting(request)
    period = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    if not can_view_result(acting, period):
        raise PermissionDenied("You cannot see this result.")
    if request.method == "POST":
        what = request.POST.get("what")
        if what == "conference":
            _run(request, lambda: results.record_conference(acting, request.user, period, _date(request.POST.get("day"))))
        elif what == "acknowledge":
            _run(request, lambda: results.acknowledge(acting, request.user, period, request.POST.get("note")))
        elif what == "reflection":
            _run(request, lambda: results.save_reflection(acting, request.user, period, request.POST.get("text")))
        elif what == "contest":
            _run(request, lambda: results.file_contest(acting, request.user, period, request.POST.get("grounds")),
                 "Contest filed.")
        return redirect("performance:result", pk=pk)
    total = period.tpe_total
    desc = scoring.description(total, period)
    ctx = {"period": period, "components": period.components.all(), "show": scoring.show,
           "total": scoring.show(total), "description": desc, "description_text": scoring.DESCRIPTIONS.get(desc, ""),
           "deductions": deductions.period_deductions(period)[0], "late": deductions.late_notes(period),
           "elsewhere": deductions.elsewhere_notes(period),
           "cap": rules.num("deduction_cap", period), "report": results.report(period),
           "is_employee": acting.pk == period.employee_id,
           "is_supervisor": results.is_conference_supervisor(acting, period),
           "contest_until": results.contest_deadline(period) if period.released_at else None,
           "today": timezone.localdate(), "plan_shown": False}
    from .score_sheet import may_print

    ctx["may_print"] = may_print(acting, period)
    plan = getattr(period, "dev_plan", None)
    if plan is not None and (not ctx["is_employee"] or plan.sent_at or plan.status != "DRAFT"):
        ctx["plan_shown"], ctx["plan"] = True, plan
    return render(request, "performance/result.html", ctx)


@login_required
def cosp_form(request, pk):
    acting = _acting(request)
    period = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    if not results.is_cosp_supervisor(acting, period) and not rules.is_hr(acting):
        raise PermissionDenied("The immediate supervisor fills in this form.")
    form = getattr(period, "cosp_form", None)
    if request.method == "POST":
        rows = [{"output": request.POST.get(f"output_{i}"), "indicator": request.POST.get(f"indicator_{i}"),
                 "q": request.POST.get(f"q_{i}"), "e": request.POST.get(f"e_{i}"), "t": request.POST.get(f"t_{i}")}
                for i in range(1, 6)]
        submit = request.POST.get("what") == "submit"
        _run(request, lambda: results.save_cosp_form(acting, request.user, period, rows, request.POST.get("remark"),
                                                     submit), "Submitted." if submit else "Saved.")
        return redirect("performance:cosp_form", pk=pk)
    outputs = list(form.outputs.all()) if form else results.previous_outputs(period)
    slots = [(i, outputs[i - 1] if i <= len(outputs) else None) for i in range(1, 6)]
    return render(request, "performance/cosp_form.html", {
        "period": period, "form": form, "slots": slots, "editable": results.is_cosp_supervisor(acting, period)
        and (form is None or form.submitted_at is None) and period.status == P.OPEN,
        "pip": form is not None and form.score is not None and form.score < 3, "show": scoring.show})


@login_required
def reviews(request):
    acting = _acting(request)
    if request.method == "POST":
        what = request.POST.get("what")
        if what in ("review_ok", "review_return"):
            period = get_object_or_404(P, pk=request.POST.get("period"))
            _run(request, lambda: results.review(acting, request.user, period, what == "review_ok",
                                                 request.POST.get("note")))
        elif what in ("contest_next", "contest_uphold", "contest_return"):
            c = get_object_or_404(Contest, pk=request.POST.get("contest"))
            _run(request, lambda: results.contest_step(acting, request.user, c, request.POST.get("note"),
                                                       upheld=what != "contest_return"))
        return redirect("performance:reviews")
    to_review = [p for p in P.objects.filter(status=P.UNDER_REVIEW).select_related("employee")
                 if acting in results.next_level_reviewers(p)]
    contests = [c for c in Contest.objects.filter(status__in=[Contest.HR, Contest.AO, Contest.COH])
                .select_related("period__employee") if results.contest_actor_ok(acting, c)]
    return render(request, "performance/reviews.html", {
        "to_review": [(p, getattr(p, "cosp_form", None), p.components.filter(component="sup").first())
                      for p in to_review],
        "contests": contests, "incidents": deductions.waiting_for(acting), "show": scoring.show})


def waiting_count(acting):
    if acting is None:
        return 0
    n = sum(1 for p in P.objects.filter(status=P.UNDER_REVIEW) if acting in results.next_level_reviewers(p))
    n += sum(1 for c in Contest.objects.filter(status__in=[Contest.HR, Contest.AO, Contest.COH])
             if results.contest_actor_ok(acting, c))
    n += len([1 for _, what in deductions.waiting_for(acting) if what != "Explain"])
    return n


@login_required
def score_sheet(request, pk):
    """Printable TPE score sheet (v1.1 §5.5)."""
    from printouts.http import pdf_response

    from . import score_sheet as sheet

    acting = _acting(request)
    period = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    if not sheet.may_print(acting, period):
        raise PermissionDenied("This score sheet cannot be printed.")
    return pdf_response(request, lambda: sheet.render_pdf(period, printed_by=request.user),
                        f"TPE-Score-Sheet_{period.employee.surname}_{period.pk}.pdf", "performance:my_results")
