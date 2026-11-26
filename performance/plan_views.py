"""Development Plans list and the plan page (PMS step 6)."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import devplans as dp, rules
from .models import DevGoal, DevPlan, EvaluationPeriod as P
from .views import _acting, _run


@login_required
def plans(request):
    acting = _acting(request)
    mine = DevPlan.objects.filter(period__employee=acting).exclude(status=DevPlan.DRAFT, sent_at=None)
    hr_all = DevPlan.objects.exclude(period__employee=acting).select_related("period__employee") \
        if rules.is_hr(acting) else None
    return render(request, "performance/plans.html", {"mine": mine, "team": dp.team_rows(acting), "hr_all": hr_all})


def _d(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


@login_required
def plan(request, pk):
    acting = _acting(request)
    period = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    the_plan = getattr(period, "dev_plan", None)
    if not dp.can_view(acting, period, the_plan) and not dp.can_start(acting, period):
        raise PermissionDenied("You cannot see this plan.")
    need, low, high = dp.counts(period)
    if request.method == "POST":
        what = request.POST.get("what")
        if what in ("draft", "send"):
            goals = [{k: request.POST.get(f"{k}_{i}") for k in ("commitment", "intervention_type", "intervention",
                                                                 "training_request_ref", "responsible", "target_date")}
                     for i in range(1, need + 1)]
            dates = [request.POST.get(f"checkin_{i}") for i in range(1, high + 1)]
            _run(request, lambda: dp.save_draft(acting, request.user, period, request.POST, goals, dates,
                                                send=what == "send"),
                 "Sent to the employee." if what == "send" else "Draft saved.")
        elif what == "agree":
            _run(request, lambda: dp.agree(acting, request.user, the_plan), "Agreed.")
        elif what == "changes":
            _run(request, lambda: dp.ask_changes(acting, request.user, the_plan, request.POST.get("note")), "Sent back.")
        elif what == "checkin":
            seqs = [g.seq for g in the_plan.goals.all()]
            _run(request, lambda: dp.record_checkin(
                acting, request.user, the_plan, _d(request.POST.get("planned_on")),
                request.POST.get("outcome") == "held", _d(request.POST.get("held_on")),
                {s: request.POST.get(f"note_{s}", "") for s in seqs},
                {s: request.POST.get(f"evidence_{s}", "") for s in seqs}), "Check-in recorded.")
        elif what == "refs":
            _run(request, lambda: dp.update_training_refs(acting, request.user, the_plan,
                                                          {g.seq: request.POST.get(f"ref_{g.seq}", "")
                                                           for g in the_plan.goals.all()}))
        elif what == "close":
            _run(request, lambda: dp.close(acting, request.user, the_plan, request.POST.get("note")), "Plan closed.")
        return redirect("performance:plan", pk=pk)
    editing = dp.can_start(acting, period) and (the_plan is None or dp.can_edit(acting, the_plan))
    pre = dp.prefill(period)
    if the_plan:
        pre.update(strengths=the_plan.strengths, growth_areas=the_plan.growth_areas)
    goals = list(the_plan.goals.all()) if the_plan else []
    form_goals = [{"i": i, "g": goals[i - 1] if i <= len(goals) else None} for i in range(1, need + 1)]
    dates = sorted({c.planned_on for g in goals for c in g.checkins.all()})
    date_slots = [dates[i] if i < len(dates) else None for i in range(high)]
    problems = dp.send_problems(period, pre, [{"commitment": g.commitment, "intervention_type": g.intervention_type,
                                               "responsible": g.responsible, "target_date": g.target_date}
                                              for g in goals], dates) if the_plan else []
    return render(request, "performance/plan.html", {
        "period": period, "plan": the_plan, "editing": editing, "pre": pre, "form_goals": form_goals, "goals": goals,
        "date_slots": date_slots, "low": low, "high": high, "problems": problems, "today": timezone.localdate(),
        "is_employee": acting.pk == period.employee_id, "is_supervisor": dp.is_supervisor(acting, period),
        "interventions": DevGoal.INTERVENTIONS, "responsibles": DevGoal.RESPONSIBLE, "not_a_pip": dp.NOT_A_PIP,
        "checkins": dp.checkin_dates(the_plan) if the_plan else [],
        "can_close": the_plan is not None and the_plan.status == DevPlan.AGREED and dp.is_supervisor(acting, period)
        and not any(c["planned"] for c in dp.checkin_dates(the_plan))})
