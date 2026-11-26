"""Monitoring, reminders, reports, renewal averages and the R&R feed (PMS
step 7; CLAUDE.md §13C; rebuilt 2026-11-26)."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from employees.holidays import add_working_days
from notifications.services import notify

from . import results, rules, scoring
from .models import DevCheckin as K, DevPlan, EvaluationPeriod as P, RaterAssignment as A, RatingWindow

SHOWN = [P.RELEASED, P.DISPUTED, P.FINAL]


def min_n():
    return int(rules.number_for("min_confidential_n"))


def window_choices():
    return RatingWindow.objects.exclude(status=RatingWindow.DRAFT).order_by("-start_date")


def periods_for(window=None, cosp_from=None, cosp_to=None):
    qs = P.objects.exclude(status__in=[P.CANCELLED, P.PLANNED]).select_related("employee")
    if window is not None:
        return qs.filter(window=window)
    return qs.filter(window__isnull=True, end_date__range=(cosp_from, cosp_to))


def area_of(emp):
    from outonpass.report import area_of as oop_area

    return oop_area(emp)


def form_minutes(a):
    if a.form == "C":
        return rules.number_for("form_minutes_c")
    if a.form == "B":
        return rules.number_for("form_minutes_b")
    return rules.number_for("form_minutes_a_supervisor" if a.group == A.SUPERVISOR else "form_minutes_a_peer")


def completion(periods):
    rows = {}
    today = timezone.localdate()
    for a in A.objects.filter(period__in=periods).exclude(status__in=[A.PROPOSED, A.NOT_SELECTED, A.VOID]) \
            .select_related("period__employee"):
        r = rows.setdefault(area_of(a.period.employee), {"area": area_of(a.period.employee), "forms": 0,
                                                        "submitted": 0, "open": 0, "overdue": 0, "released": 0})
        r["forms"] += 1
        if a.status == A.SUBMITTED:
            r["submitted"] += 1
        elif a.status in A.LIVE:
            r["open"] += 1
            if a.due_on and a.due_on < today:
                r["overdue"] += 1
    for r in rows.values():
        r["pct"] = round(100 * r["submitted"] / r["forms"]) if r["forms"] else 0
    return sorted(rows.values(), key=lambda r: r["area"])


def load(periods):
    expected = actual = Decimal("0")
    for a in A.objects.filter(period__in=periods).exclude(status__in=[A.PROPOSED, A.NOT_SELECTED, A.VOID]):
        expected += form_minutes(a)
        if a.status == A.SUBMITTED and a.first_shown_at and a.submitted_at:
            actual += Decimal((a.submitted_at - a.first_shown_at).total_seconds()) / 60
    conf = sum(1 for p in periods if p.period_type in (P.SEMESTRAL, P.PRE_EXPIRY))
    checks = sum(1 for p in periods if p.period_type in (P.MONTH1, P.QUARTER))
    expected += conf * rules.number_for("conference_minutes") + checks * rules.number_for("checkin_minutes")
    return {"expected": round(expected), "actual": round(actual), "conferences": conf, "checkins": checks}


def queues(periods):
    q = {"flags": 0, "comments": 0, "certified": 0, "dtr": 0, "cosp_forms": 0, "reviews": 0, "to_release": 0,
         "conferences": 0, "plans": 0, "contests": 0}
    for p in periods:
        q["flags"] += p.raters.filter(flag_review="PENDING").count()
        q["comments"] += results.pending_comments(p).count()
        if not rules.is_cosp(p.employee) and p.certified_verified_by_id is None:
            q["certified"] += 1
        if p.track == P.RANK and p.dtr_verified_by_id is None:
            q["dtr"] += 1
        if rules.is_cosp(p.employee) and (getattr(p, "cosp_form", None) is None or p.cosp_form.submitted_at is None):
            q["cosp_forms"] += 1
        q["reviews"] += p.status == P.UNDER_REVIEW
        q["to_release"] += p.status == P.COMPUTED
        q["contests"] += p.status == P.DISPUTED
        if p.status in SHOWN:
            q["conferences"] += p.conference_on is None
            plan = getattr(p, "dev_plan", None)
            q["plans"] += plan is None or plan.status == DevPlan.DRAFT
    return q


def unit_averages(periods):
    by = {}
    for p in periods:
        if p.status in SHOWN and p.tpe_total is not None:
            by.setdefault(area_of(p.employee), []).append(p.tpe_total)
    out = []
    for area, vals in sorted(by.items()):
        out.append({"area": area, "n": len(vals), "mean": scoring.show(sum(vals) / len(vals))
                    if len(vals) >= min_n() else None})
    return out


def team_average(acting):
    vals = [p.tpe_total for p in P.objects.filter(status__in=SHOWN).exclude(tpe_total=None).select_related("employee")
            if results.is_conference_supervisor(acting, p)]
    if len(vals) < min_n():
        return None
    return scoring.show(sum(vals) / len(vals))


def renewal_average(contract):
    rows = P.objects.filter(contract=contract).exclude(status__in=[P.CANCELLED, P.PLANNED])
    scored = [p for p in rows if p.status in (P.RELEASED, P.FINAL) and p.tpe_total is not None]
    waiting = [p for p in rows if p.status == P.DISPUTED]
    avg = scoring.show(sum(p.tpe_total for p in scored) / len(scored)) if scored else None
    return {"contract": contract, "average": avg, "periods": scored, "waiting": waiting}


def renewal_rows():
    from outonpass.models import CospContractPeriod

    since = timezone.localdate() - timedelta(days=60)
    return [renewal_average(c) for c in CospContractPeriod.objects.filter(voided_at__isnull=True, end_date__gte=since)
            .select_related("employee").order_by("end_date")]


def summary(window):
    from .models import Indicator, Response

    periods = [p for p in periods_for(window) if p.status in SHOWN and p.tpe_total is not None]
    n, small = len(periods), min_n()
    mean = scoring.show(sum(p.tpe_total for p in periods) / n) if n >= small else None
    bands = {}
    for p in periods:
        bands[scoring.description(p.tpe_total, p)] = bands.get(scoring.description(p.tpe_total, p), 0) + 1
    band_rows = [{"band": b, "n": (c if c >= small else f"fewer than {small}")}
                 for b, c in ((b, bands.get(b, 0)) for b in scoring.DESCRIPTIONS)]
    by_section = {}
    for p in periods:
        for s in p.employee.sections.all():
            by_section.setdefault(s.name, []).append(p.tpe_total)
    sections = [{"section": k, "n": len(v), "mean": scoring.show(sum(v) / len(v)) if len(v) >= small else None}
                for k, v in sorted(by_section.items())]
    counted = A.objects.filter(period__in=periods, status=A.SUBMITTED).exclude(group=A.SELF).exclude(
        flag_review="VOIDED")
    items = []
    for ind in Indicator.objects.filter(code__regex=r"^[AB]\d+$"):
        vals = list(Response.objects.filter(assignment__in=counted, indicator=ind, not_observed=False)
                    .exclude(value=None).values_list("value", flat=True))
        if len(vals) >= small:
            items.append({"text": ind.wording_2 or ind.wording_1, "mean": Decimal(sum(vals)) / len(vals)})
    items.sort(key=lambda x: -x["mean"])
    plans = DevPlan.objects.filter(period__in=periods)
    held = K.objects.filter(goal__plan__in=plans, status=K.HELD).count()
    missed = K.objects.filter(goal__plan__in=plans, status=K.MISSED).count()
    return {"window": window, "n": n, "mean": mean, "bands": band_rows, "sections": sections,
            "strongest": items[:5], "gaps": list(reversed(items[-5:])) if len(items) > 5 else [],
            "plans": plans.count(), "agreed": plans.exclude(status=DevPlan.DRAFT).count(), "held": held,
            "missed": missed}


def summary_tables(s):
    show = lambda v: "—" if v is None else str(v)  # noqa: E731
    return [
        ("Overview", ["Item", "Value"], [["Results counted", s["n"]], ["Mean TPE", show(s["mean"])],
                                         ["Development plans", s["plans"]], ["Plans agreed", s["agreed"]],
                                         ["Check-ins held", s["held"]], ["Check-ins missed", s["missed"]]]),
        ("Results by performance description", ["Description", "Employees"], [[b["band"], b["n"]] for b in s["bands"]]),
        ("Mean TPE by section", ["Section", "Results", "Mean"], [[x["section"], x["n"], show(x["mean"])]
                                                                 for x in s["sections"]]),
        ("Strongest items", ["Item", "Mean"], [[x["text"], f"{x['mean']:.2f}"] for x in s["strongest"]]),
        ("Common gaps", ["Item", "Mean"], [[x["text"], f"{x['mean']:.2f}"] for x in s["gaps"]]),
    ]


def rr_feed(window):
    header = ["Employee ID", "Employee", "Position", "Section / unit", "PHRMO-certified rating", "TPE total",
              "Performance description", "Period"]
    rows = []
    for p in periods_for(window).filter(status=P.FINAL).order_by("employee__surname"):
        rows.append([p.employee.employee_id, p.employee.full_name, p.employee.position, area_of(p.employee),
                     str(p.certified_rating) if p.certified_rating is not None else "",
                     str(scoring.show(p.tpe_total)), scoring.description(p.tpe_total, p),
                     f"{p.start_date:%b %d, %Y} - {p.end_date:%b %d, %Y}"])
    return header, rows


# -- reminders -----------------------------------------------------------------------------------

def _mark(a, code):
    sent = [x for x in a.reminders_sent.split(",") if x]
    if code in sent:
        return False
    sent.append(code)
    a.reminders_sent = ",".join(sent)
    a.save(update_fields=["reminders_sent"])
    return True


def send_reminders(today=None):
    """(rater reminders sent, check-in reminders sent). One notice per form per point."""
    today = today or timezone.localdate()
    n_forms = n_checks = 0
    for a in A.objects.filter(status__in=A.LIVE).select_related("period__employee", "period__window"):
        p = a.period
        if a.due_on is None:
            continue
        start = p.window.opens_on if p.window_id else p.end_date
        span = max((a.due_on - start).days, 1)
        share = Decimal((today - start).days) / span
        due_points = []
        if share >= rules.num("reminder_share_1", p):
            due_points.append("1")
        if share >= rules.num("reminder_share_2", p):
            due_points.append("2")
        if (a.due_on - today).days <= int(rules.num("reminder_days_before_close", p)):
            due_points.append("close")
        new = [code for code in due_points if code not in a.reminders_sent.split(",")]
        if new:
            for code in new:
                _mark(a, code)
            who = "your self-assessment" if a.group == A.SELF else f"a rating form for {p.employee.full_name}"
            notify(a.rater, f"Reminder: {who} is due {a.due_on:%b %d, %Y}.", "/performance/tasks/")
            n_forms += 1
    for k in K.objects.filter(status=K.PLANNED, reminded_on__isnull=True, goal__plan__status=DevPlan.AGREED) \
            .select_related("goal__plan__period__employee", "goal__plan__supervisor"):
        plan = k.goal.plan
        if 0 <= (k.planned_on - today).days <= int(rules.num("checkin_reminder_days", plan.period)):
            K.objects.filter(goal__plan=plan, planned_on=k.planned_on).update(reminded_on=today)
            notify([plan.supervisor, plan.period.employee], f"Reminder: development-plan check-in on "
                                                            f"{k.planned_on:%b %d, %Y}.", f"/performance/plans/{plan.period_id}/")
            n_checks += 1
    return n_forms, n_checks
