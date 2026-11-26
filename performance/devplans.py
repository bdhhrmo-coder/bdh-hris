"""Development plans (PMS step 6; CLAUDE.md §13C; rebuilt 2026-11-26).

The supervisor drafts the plan after release (prefilled from the
developmental report), sends it once the feedback conference is recorded,
the employee agrees or asks for changes, the supervisor records each
check-in date as held or missed and closes the plan. HR reads every plan
(never their own); the AO and the COH have no access.

Missed check-ins (owner decision 2026-11-26, D11 Option A): a check-in still
planned `checkin_missed_days` working days after its date is marked missed
by the daily job, which also files an X7 incident against the supervisor
(module task not done). The supervisor may still record it afterwards; it is
then kept as held but "late". The incident follows the normal route."""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from employees.holidays import add_working_days
from notifications.services import notify

from . import results, rules
from .models import DevCheckin as K, DevGoal, DevPlan, EvaluationPeriod as P

SHOWN = [P.RELEASED, P.DISPUTED, P.FINAL]
NOT_A_PIP = ("This is a development plan, not a PIP. If the performance rating is below Satisfactory, the PIP under "
             "BDH-HR-PM-01 applies.")


def plan_url(period):
    return f"/performance/plans/{period.pk}/"


def counts(period):
    return (int(rules.num("dev_commitments", period)), int(rules.num("dev_checkins_min", period)),
            int(rules.num("dev_checkins_max", period)))


def is_supervisor(acting, period):
    return results.is_conference_supervisor(acting, period)


def can_start(acting, period):
    return period.status in SHOWN and is_supervisor(acting, period)


def can_view(acting, period, plan):
    if is_supervisor(acting, period):
        return True
    if acting.pk == period.employee_id:
        return plan is not None and (plan.sent_at is not None or plan.status != DevPlan.DRAFT)
    return rules.is_hr(acting)


def can_edit(acting, plan):
    return plan.status == DevPlan.DRAFT and plan.sent_at is None and is_supervisor(acting, plan.period)


def prefill(period):
    """Strengths and growth areas from the report, one per line, and the support
    the employee asked for on Form C (item C4), which v1.1 copies into the plan."""
    rep = results.report(period)
    return {"strengths": "\n".join(s["text"] for s in rep["strengths"][:2]),
            "growth_areas": "\n".join(s["text"] for s in rep["growth"][:2]),
            "support": (rep["self_c"].get("C4") or "").strip()}


def send_problems(period, data, goals, dates):
    need, low, high = counts(period)
    out = []
    if not period.conference_on:
        out.append("Record the feedback conference first.")
    real = [g for g in goals if (g.get("commitment") or "").strip()]
    if len(real) != need:
        out.append(f"Fill in all {need} commitments.")
    for g in real:
        if not g.get("intervention_type") or not g.get("responsible") or not g.get("target_date"):
            out.append("Each commitment needs an intervention, a responsible person and a target date.")
            break
    if not low <= len(dates) <= high:
        out.append(f"Set {low} to {high} check-in date(s).")
    return out


def _d(raw):
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


@transaction.atomic
def save_draft(acting, user, period, data, goal_rows, checkin_values, send=False):
    if not can_start(acting, period):
        raise ValidationError("Only the immediate supervisor prepares the plan, after the result is released.")
    plan, _ = DevPlan.objects.get_or_create(period=period, defaults={"supervisor": acting})
    if not can_edit(acting, plan):
        raise ValidationError("This plan can no longer be edited.")
    today = timezone.localdate()
    dates = sorted({d for d in (_d(v) for v in checkin_values) if d})
    for g in goal_rows:
        t = _d(g.get("target_date"))
        if (g.get("commitment") or "").strip() and t and t < today:
            raise ValidationError("A target date cannot be in the past.")
    if any(d < today for d in dates):
        raise ValidationError("Check-in dates cannot be in the past.")
    if send:
        problems = send_problems(period, data, goal_rows, dates)
        if problems:
            raise ValidationError(" ".join(problems))
    plan.strengths, plan.growth_areas = (data.get("strengths") or "").strip(), (data.get("growth_areas") or "").strip()
    plan.supervisor = acting
    plan.goals.all().delete()
    for i, g in enumerate([g for g in goal_rows if (g.get("commitment") or "").strip()], start=1):
        goal = DevGoal.objects.create(plan=plan, seq=i, commitment=g["commitment"].strip(),
                                      intervention_type=g.get("intervention_type") or "COACHING",
                                      intervention=(g.get("intervention") or "").strip()[:200],
                                      training_request_ref=(g.get("training_request_ref") or "").strip()[:80],
                                      responsible=g.get("responsible") or "EMPLOYEE",
                                      target_date=_d(g.get("target_date")) or today)
        for d in dates:
            K.objects.create(goal=goal, planned_on=d)
    if send:
        plan.sent_at, plan.change_note = timezone.now(), ""
        notify(period.employee, "Your development plan is ready for your agreement.", plan_url(period))
        rules.log(period, "plan_sent", user)
    plan.save()
    return plan


def agree(acting, user, plan):
    if acting.pk != plan.period.employee_id:
        raise ValidationError("Only the employee agrees to the plan.")
    if plan.status != DevPlan.DRAFT or plan.sent_at is None:
        raise ValidationError("The plan is not waiting for your agreement.")
    plan.status, plan.agreed_at = DevPlan.AGREED, timezone.now()
    plan.save(update_fields=["status", "agreed_at"])
    rules.log(plan.period, "plan_agreed", user)
    notify(plan.supervisor, f"{plan.period.employee.full_name} agreed to the development plan.", plan_url(plan.period))


def ask_changes(acting, user, plan, note):
    if acting.pk != plan.period.employee_id:
        raise ValidationError("Only the employee asks for changes.")
    if plan.status != DevPlan.DRAFT or plan.sent_at is None:
        raise ValidationError("The plan is not waiting for your answer.")
    if not (note or "").strip():
        raise ValidationError("Say what should change.")
    plan.sent_at, plan.change_note = None, note.strip()
    plan.save(update_fields=["sent_at", "change_note"])
    rules.log(plan.period, "plan_changes_asked", user, note)
    notify(plan.supervisor, f"{plan.period.employee.full_name} asked for changes to the development plan.",
           plan_url(plan.period))


@transaction.atomic
def record_checkin(acting, user, plan, planned_on, held, held_on, notes, evidence):
    if not is_supervisor(acting, plan.period):
        raise ValidationError("The supervisor records check-ins.")
    if plan.status != DevPlan.AGREED:
        raise ValidationError("Only an agreed plan has check-ins.")
    rows = list(K.objects.filter(goal__plan=plan, planned_on=planned_on).select_related("goal"))
    if not rows:
        raise ValidationError("No check-in on that date.")
    if any(r.status == K.HELD for r in rows):
        raise ValidationError("Already recorded.")
    late = any(r.auto_missed_at for r in rows)
    if not held and late:
        raise ValidationError("This check-in was already marked missed by the system.")
    if held:
        if held_on is None or held_on > timezone.localdate() or (plan.agreed_at and held_on < plan.agreed_at.date()):
            raise ValidationError("The date held must be between the plan's agreement and today.")
    elif not any((notes.get(r.goal.seq) or "").strip() for r in rows):
        raise ValidationError("Say why the check-in was missed.")
    for r in rows:
        r.status = K.HELD if held else K.MISSED
        r.held_on, r.note, r.late = (held_on if held else None), (notes.get(r.goal.seq) or "").strip(), late
        r.save()
        ev = (evidence.get(r.goal.seq) or "").strip()
        if held and ev:
            r.goal.evidence = ev
            r.goal.save(update_fields=["evidence"])
    rules.log(plan.period, "plan_checkin", user, f"{planned_on:%b %d, %Y}: {'held' if held else 'missed'}"
              + (" (late)" if late else ""))


def update_training_refs(acting, user, plan, refs):
    if not is_supervisor(acting, plan.period) or plan.status != DevPlan.AGREED:
        raise ValidationError("The supervisor adds references to an agreed plan.")
    for g in plan.goals.all():
        if g.seq in refs:
            g.training_request_ref = (refs[g.seq] or "").strip()[:80]
            g.save(update_fields=["training_request_ref"])


def close(acting, user, plan, note=""):
    if not is_supervisor(acting, plan.period) or plan.status != DevPlan.AGREED:
        raise ValidationError("The supervisor closes an agreed plan.")
    if K.objects.filter(goal__plan=plan, status=K.PLANNED).exists():
        raise ValidationError("Record every check-in date first.")
    plan.status, plan.closed_at, plan.close_note = DevPlan.CLOSED, timezone.now(), (note or "").strip()
    plan.save(update_fields=["status", "closed_at", "close_note"])
    rules.log(plan.period, "plan_closed", user, note)


def checkin_dates(plan):
    out = {}
    for r in K.objects.filter(goal__plan=plan).select_related("goal").order_by("planned_on", "goal__seq"):
        out.setdefault(r.planned_on, []).append(r)
    return [{"date": d, "rows": rows, "planned": any(r.status == K.PLANNED for r in rows),
             "auto_missed": any(r.auto_missed_at for r in rows) and not any(r.status == K.HELD for r in rows)}
            for d, rows in out.items()]


@transaction.atomic
def auto_miss(today=None):
    """Daily job: mark check-ins missed and file one X7 incident per plan and date."""
    from .deductions import file as file_deduction
    from .models import DeductionCriterion

    today = today or timezone.localdate()
    crit = DeductionCriterion.objects.filter(code="X7", is_active=True).first()
    done = 0
    rows = K.objects.filter(status=K.PLANNED, goal__plan__status=DevPlan.AGREED).select_related(
        "goal__plan__period__employee", "goal__plan__supervisor")
    by_key = {}
    for r in rows:
        days = int(rules.num("checkin_missed_days", r.goal.plan.period))
        if add_working_days(r.planned_on, days) < today:
            by_key.setdefault((r.goal.plan_id, r.planned_on), []).append(r)
    for (plan_id, day), items in by_key.items():
        plan = items[0].goal.plan
        deduction = None
        if crit is not None:
            try:
                deduction = file_deduction(None, None, plan.supervisor, crit, day,
                                           f"Development plan check-in of {plan.period.employee.full_name} planned on "
                                           f"{day:%b %d, %Y} was not recorded in time.", crit.options()[0],
                                           auto_source="missed development-plan check-in", today=today)
            except ValidationError:
                deduction = None  # X7 does not apply to this supervisor's track
        for r in items:
            r.status, r.auto_missed_at, r.deduction = K.MISSED, timezone.now(), deduction
            r.save(update_fields=["status", "auto_missed_at", "deduction"])
        rules.log(plan.period, "plan_checkin_missed", None, f"{day:%b %d, %Y}: marked missed by the system")
        notify([plan.supervisor, plan.period.employee], f"The development-plan check-in of {day:%b %d, %Y} was "
               "marked missed. The supervisor can still record it (it is kept as late).", plan_url(plan.period))
        done += 1
    return done


def team_rows(acting):
    out = []
    for p in P.objects.filter(status__in=SHOWN).select_related("employee"):
        if is_supervisor(acting, p):
            out.append((p, getattr(p, "dev_plan", None)))
    return out
