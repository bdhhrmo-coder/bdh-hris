"""Inputs, compute, review, release, conference and contests (PMS step 4;
CLAUDE.md §13C; rebuilt 2026-11-26)."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from employees.holidays import add_working_days, working_days
from notifications.services import notify

from . import raters, rules, scoring
from .models import (Comment, ComponentScore, Contest, CospForm, EvaluationPeriod as P, PerfOutput,
                     RaterAssignment as A, Response)

RESULTS_URL = "/performance/results/"


def _not_own(acting, period):
    if acting.pk == period.employee_id:
        raise ValidationError("Never on your own period.")


# -- inputs -----------------------------------------------------------------------------------

def encode_certified(acting, user, period, rating, ref):
    _not_own(acting, period)
    if not rules.is_hr(acting):
        raise ValidationError("HR encodes the certified rating.")
    if rules.is_cosp(period.employee):
        raise ValidationError("COSP employees have no PHRMO-certified rating; they use the COSP Performance Form.")
    try:
        rating = Decimal(rating)
    except (InvalidOperation, TypeError):
        raise ValidationError("Enter the rating as a number.")
    if not Decimal("1") <= rating <= Decimal("5"):
        raise ValidationError("The certified rating is between 1.00 and 5.00.")
    if not (ref or "").strip():
        raise ValidationError("Enter the certificate reference.")
    period.certified_rating, period.certificate_ref = rating, ref.strip()
    period.certified_encoded_by, period.certified_verified_by = user, None
    period.save(update_fields=["certified_rating", "certificate_ref", "certified_encoded_by", "certified_verified_by"])
    rules.log(period, "certified_encoded", user, f"{rating} ({ref.strip()})")


def encode_dtr(acting, user, period, absences, tardy):
    _not_own(acting, period)
    if not rules.is_hr(acting):
        raise ValidationError("HR encodes the DTR counts.")
    if period.track != P.RANK:
        raise ValidationError("Attendance is a rank-and-file component only.")
    try:
        absences, tardy = int(absences), int(tardy)
    except (TypeError, ValueError):
        raise ValidationError("Enter whole numbers.")
    if absences < 0 or tardy < 0:
        raise ValidationError("Counts cannot be negative.")
    period.absence_days, period.tardy_days, period.dtr_encoded_by, period.dtr_verified_by = absences, tardy, user, None
    period.save(update_fields=["absence_days", "tardy_days", "dtr_encoded_by", "dtr_verified_by"])
    rules.log(period, "dtr_encoded", user, f"{absences} unauthorized absence(s), {tardy} tardy day(s); undertime "
                                           "never counts")


def verify_input(acting, user, period, which):
    _not_own(acting, period)
    if not rules.is_hr_admin(acting):
        raise ValidationError("An HR Administrator verifies.")
    enc = period.certified_encoded_by_id if which == "certified" else period.dtr_encoded_by_id
    if enc is None:
        raise ValidationError("Nothing to verify yet.")
    if enc == user.pk:
        raise ValidationError("Another HR Administrator verifies what you encoded.")
    setattr(period, f"{'certified' if which == 'certified' else 'dtr'}_verified_by", user)
    period.save()
    rules.log(period, f"{which}_verified", user)


# -- COSP Performance Form ------------------------------------------------------------------------

def is_cosp_supervisor(acting, period):
    return acting in raters.immediate_supervisors(period.employee, period.end_date)


def cosp_forms_waiting(acting):
    out = []
    for p in P.objects.filter(status=P.OPEN, employee__employment_status="COSP").select_related("employee"):
        f = getattr(p, "cosp_form", None)
        if (f is None or f.submitted_at is None) and is_cosp_supervisor(acting, p):
            out.append(p)
    return out


def previous_outputs(period):
    prev = (CospForm.objects.filter(period__employee=period.employee, period__start_date__lt=period.start_date)
            .exclude(period=period).order_by("-period__start_date").first())
    return list(prev.outputs.all()) if prev else []


def _dim(raw, allow_na):
    raw = (raw or "").strip()
    if raw == "" and allow_na:
        return None
    if raw in {"1", "2", "3", "4", "5"}:
        return int(raw)
    raise ValidationError("Rate every output 1-5 (efficiency and timeliness may be N/A).")


@transaction.atomic
def save_cosp_form(acting, user, period, rows, remark, submit):
    if not is_cosp_supervisor(acting, period) or acting.pk == period.employee_id:
        raise ValidationError("The immediate supervisor fills in the COSP Performance Form.")
    if period.status != P.OPEN:
        raise ValidationError("The period is not open.")
    form, _ = CospForm.objects.get_or_create(period=period, defaults={"supervisor": acting})
    if form.submitted_at:
        raise ValidationError("Already submitted.")
    rows = [r for r in rows if (r.get("output") or "").strip()]
    if submit and not 3 <= len(rows) <= 5:
        raise ValidationError("Give 3 to 5 key outputs.")
    form.outputs.all().delete()
    averages, low = [], False
    for i, r in enumerate(rows, start=1):
        q = _dim(r.get("q"), False) if submit or r.get("q") else None
        e = _dim(r.get("e"), True) if submit or r.get("e") else None
        t = _dim(r.get("t"), True) if submit or r.get("t") else None
        PerfOutput.objects.create(form=form, seq=i, output=r["output"].strip()[:200],
                                  success_indicator=(r.get("indicator") or "").strip()[:300],
                                  quality=q, efficiency=e, timeliness=t)
        dims = [x for x in (q, e, t) if x is not None]
        if dims:
            averages.append(Decimal(sum(dims)) / len(dims))
            low = low or min(dims) <= 2
    form.remark = (remark or "").strip()
    if submit:
        if any(not (r.get("indicator") or "").strip() for r in rows):
            raise ValidationError("Give the success indicator of every output.")
        if low and not form.remark:
            raise ValidationError("A remark is required when any output is rated 2 or below.")
        form.score = sum(averages) / len(averages)
        form.submitted_at = timezone.now()
        rules.log(period, "cosp_form", user, f"COSP Performance Form submitted ({scoring.show(form.score)})")
    form.save()
    return form


# -- compute, flags, comments, review, release -----------------------------------------------------

def compute_ready(period):
    if period.window_id:
        return period.window.status == "CLOSED", "after the rating window closes"
    pending = period.raters.filter(status__in=A.LIVE).exists()
    due = raters.due_date(period)
    return (not pending) or timezone.localdate() > due, f"once every form is in or after {due:%b %d, %Y}"


@transaction.atomic
def compute(acting, user, period):
    _not_own(acting, period)
    if not rules.is_hr(acting):
        raise ValidationError("HR computes results.")
    if period.status not in (P.OPEN, P.COMPUTED, P.UNDER_REVIEW):
        raise ValidationError("This period cannot be computed now.")
    ok, when = compute_ready(period)
    if not ok:
        raise ValidationError(f"Compute {when}.")
    result = scoring.score(period)
    if result["missing"]:
        raise ValidationError("Still needed: " + "; ".join(sorted(set(result["missing"]))) + ".")
    period.components.all().delete()
    for p in result["parts"]:
        ComponentScore.objects.create(period=period, component=p["component"], label=p["label"], mean=p["mean"],
                                      n=p["n"], pooled=p["pooled"], skipped=p["skipped"], note=p["note"][:255],
                                      weight_nominal=p["weight_nominal"], weight_used=p["weight_used"],
                                      points=p["points"], sort_order=p["sort_order"])
    att = next((p for p in result["parts"] if p["component"] == "att"), None)
    period.attendance_score = att["mean"] if att else None
    period.subtotal, period.deduction_total, period.tpe_total = result["subtotal"], result["deductions"], result["total"]
    period.computed_at = timezone.now()
    period.status = P.UNDER_REVIEW if rules.is_cosp(period.employee) else P.COMPUTED
    period.reviewed_by, period.reviewed_at = None, None
    period.save()
    flags = scoring.mark_flags(period)
    rules.log(period, "computed", user, f"TPE {scoring.show(result['total'])}" + (f"; {flags} flag(s)" if flags else ""))
    reviewers = next_level_reviewers(period)
    if period.status == P.UNDER_REVIEW:
        notify(reviewers, f"Next-level review: {period.employee.full_name} ({period.get_period_type_display()}).",
               "/performance/reviews/")
    return result


def decide_flag(acting, user, a, keep, reason=""):
    period = a.period
    if not rules.is_hr_admin(acting) or acting.pk == period.employee_id:
        raise ValidationError("An HR Administrator reviews flags (never on their own period).")
    if a.flag_review != "PENDING":
        raise ValidationError("No flag waiting.")
    if not keep and not (reason or "").strip():
        raise ValidationError("Type the reason for voiding it. A rating is never voided just because it is low.")
    a.flag_review, a.flag_note = ("KEPT" if keep else "VOIDED"), (reason or "").strip()[:255]
    a.save(update_fields=["flag_review", "flag_note"])
    rules.log(period, "flag_" + ("kept" if keep else "voided"), user, f"{a.get_group_display()} form: {a.flags}"
              + (f" - {reason.strip()}" if reason else ""))
    if not keep and period.status in (P.COMPUTED, P.UNDER_REVIEW):
        period.status = P.OPEN
        period.save(update_fields=["status"])


def screen_comment(acting, user, c, action, text="", reason=""):
    period = c.assignment.period
    if not rules.is_hr_admin(acting) or acting.pk == period.employee_id:
        raise ValidationError("An HR Administrator screens comments (never on their own period).")
    if action == "release":
        c.status, c.released_text = Comment.RELEASED, c.raw_text
    elif action == "edit":
        if not (reason or "").strip() or not (text or "").strip():
            raise ValidationError("Give the edited text and the reason.")
        c.status, c.released_text = Comment.EDITED, text.strip()
    elif action == "withhold":
        if not (reason or "").strip():
            raise ValidationError("Type the reason for withholding it.")
        c.status, c.released_text = Comment.WITHHELD, ""
    else:
        raise ValidationError("Unknown action.")
    c.screened_by, c.reason = user, (reason or "").strip()[:255]
    c.save()
    rules.log(period, f"comment_{action}", user, reason)


def pending_comments(period):
    return Comment.objects.filter(assignment__period=period, status=Comment.PENDING,
                                  assignment__status=A.SUBMITTED)


def next_level_reviewers(period):
    sups = raters.immediate_supervisors(period.employee, period.end_date)
    out = []
    for s in sups:
        for r in raters.immediate_supervisors(s, period.end_date) or []:
            if r.pk != period.employee_id and r not in out:
                out.append(r)
    if not out:
        from .deductions import _ao_list

        out = _ao_list([period.employee] + sups)
    return out


def review(acting, user, period, approve, note=""):
    if period.status != P.UNDER_REVIEW:
        raise ValidationError("Not waiting for next-level review.")
    if acting not in next_level_reviewers(period):
        raise ValidationError("Only the next level up from the supervisor reviews.")
    if not approve and not (note or "").strip():
        raise ValidationError("Type the note for returning it.")
    period.reviewed_by, period.reviewed_at, period.review_note = user, timezone.now(), (note or "").strip()
    if approve:
        period.status = P.COMPUTED
    else:
        period.status = P.OPEN
        form = getattr(period, "cosp_form", None)
        if form:
            form.submitted_at = None
            form.save(update_fields=["submitted_at"])
        notify(raters.immediate_supervisors(period.employee, period.end_date),
               f"The COSP Performance Form for {period.employee.full_name} was returned: \"{note.strip()}\".",
               "/performance/tasks/")
    period.save()
    rules.log(period, "review_" + ("approved" if approve else "returned"), user, note)


def release_problems(period):
    out = []
    if period.status != P.COMPUTED:
        out.append("the period is not computed (or still under next-level review)")
    if period.raters.filter(flag_review="PENDING").exists():
        out.append("flags are waiting")
    if pending_comments(period).exists():
        out.append("comments are waiting to be screened")
    return out


def release(acting, user, period):
    _not_own(acting, period)
    if not rules.is_hr_admin(acting):
        raise ValidationError("An HR Administrator releases results.")
    problems = release_problems(period)
    if problems:
        raise ValidationError("Not yet: " + "; ".join(problems) + ".")
    period.status, period.released_at, period.released_by = P.RELEASED, timezone.now(), user
    period.save(update_fields=["status", "released_at", "released_by"])
    rules.log(period, "released", user)
    notify(period.employee, "Your performance result is released. Please read it before your feedback conference.",
           f"{RESULTS_URL}{period.pk}/")
    notify(raters.immediate_supervisors(period.employee, period.end_date),
           f"{period.employee.full_name}'s performance result is released: schedule the feedback conference.",
           f"{RESULTS_URL}{period.pk}/")


# -- after release ----------------------------------------------------------------------------------

def is_conference_supervisor(acting, period):
    return acting in raters.immediate_supervisors(period.employee, period.end_date)


def record_conference(acting, user, period, day):
    if not is_conference_supervisor(acting, period):
        raise ValidationError("The immediate supervisor records the conference.")
    if period.status not in (P.RELEASED, P.DISPUTED, P.FINAL):
        raise ValidationError("Only after release.")
    if day > timezone.localdate() or (period.released_at and day < period.released_at.date()):
        raise ValidationError("The conference date must be between the release and today.")
    period.conference_on, period.conference_by = day, acting
    period.save(update_fields=["conference_on", "conference_by"])
    rules.log(period, "conference", user, f"{day:%b %d, %Y}")


def acknowledge(acting, user, period, note=""):
    if acting.pk != period.employee_id:
        raise ValidationError("Only the employee acknowledges receipt.")
    period.acknowledged_at, period.disagreement_note = timezone.now(), (note or "").strip()
    period.save(update_fields=["acknowledged_at", "disagreement_note"])
    rules.log(period, "acknowledged", user, "with a disagreement note" if note else "")


def save_reflection(acting, user, period, text):
    if acting.pk != period.employee_id:
        raise ValidationError("Only the employee writes the reflection.")
    period.reflection = (text or "").strip()
    period.save(update_fields=["reflection"])


def contest_deadline(period):
    return add_working_days(period.released_at.date(), int(rules.num("dispute_days", period)))


def file_contest(acting, user, period, grounds):
    if acting.pk != period.employee_id:
        raise ValidationError("Only the employee contests their result.")
    if period.status != P.RELEASED or timezone.localdate() > contest_deadline(period):
        raise ValidationError("The contest window is closed.")
    if not (grounds or "").strip():
        raise ValidationError("Give the grounds.")
    Contest.objects.create(period=period, grounds=grounds.strip())
    period.status = P.DISPUTED
    period.save(update_fields=["status"])
    rules.log(period, "contest", user)
    from leave.permissions import hr_employees

    notify([h for h in hr_employees() if h.pk != period.employee_id],
           f"{period.employee.full_name} contested a performance result.", "/performance/reviews/")


def contest_actor_ok(acting, c):
    emp = c.period.employee
    if acting.pk == emp.pk:
        return False
    if c.status == Contest.HR:
        return rules.is_hr(acting)
    if c.status == Contest.AO:
        if emp.has_role("ADMINISTRATIVE_OFFICER"):
            return rules.is_coh(acting)
        return rules.is_ao(acting)
    if c.status == Contest.COH:
        return rules.is_coh(acting)
    return False


@transaction.atomic
def contest_step(acting, user, c, note, upheld=True):
    if not contest_actor_ok(acting, c):
        raise ValidationError("Not your step.")
    if not (note or "").strip():
        raise ValidationError("Type a note.")
    period = c.period
    if c.status == Contest.HR:
        c.status = Contest.AO
    elif c.status == Contest.AO:
        c.status = Contest.COH
    else:
        c.status, c.decided_at = (Contest.UPHELD if upheld else Contest.RETURNED), timezone.now()
        if upheld:
            period.status, period.final_at = P.FINAL, timezone.now()
        else:
            period.status, period.released_at, period.released_by = P.OPEN, None, None
        period.save(update_fields=["status", "final_at", "released_at", "released_by"])
        notify(period.employee, f"Your contest was decided: {'result upheld' if upheld else 'returned for correction'}.",
               f"{RESULTS_URL}{period.pk}/")
    c.save()
    rules.log(period, f"contest_{c.status.lower()}", user, note)


def finalize_due(today=None):
    from .deductions import finalize_due as ded_final

    today = today or timezone.localdate()
    n = 0
    for p in P.objects.filter(status=P.RELEASED).exclude(released_at=None):
        if today > contest_deadline(p):
            p.status, p.final_at = P.FINAL, timezone.now()
            p.save(update_fields=["status", "final_at"])
            rules.log(p, "final", None, "contest window closed")
            n += 1
    ded_final(today)
    return n


# -- the developmental report ---------------------------------------------------------------------

def shown_assignments(period):
    """Counted forms from groups that may be shown (identified, or confidential with n >= minimum)."""
    min_n = int(rules.num("min_confidential_n", period))
    out = {}
    for groups, key in (([A.SUPERVISOR], "Immediate supervisor"), ([A.TOP], "Top management"),
                        ([A.PEER, A.POOLED], "Peers"), ([A.SUP_PEER], "Supervisor peers"),
                        ([A.SUBORDINATE], "Subordinates")):
        rows = [a for a in scoring.counted(period, groups) if scoring.rater_average(a, period)[0] is not None]
        confidential = groups[0] in A.CONFIDENTIAL
        if rows and (not confidential or len(rows) >= min_n):
            out[key] = rows
    return out


def report(period):
    from .models import Indicator

    shown = shown_assignments(period)
    all_rows = [a for rows in shown.values() for a in rows]
    form = "A" if period.track == P.RANK else "B"
    wording = all_rows[0].wording if all_rows else "1"
    items = []
    for ind in Indicator.objects.filter(code__in=scoring.scored_codes(form)).select_related("competency"):
        vals = list(Response.objects.filter(assignment__in=all_rows, indicator=ind, not_observed=False)
                    .exclude(value=None).values_list("value", flat=True))
        if vals:
            items.append({"text": ind.wording(wording), "competency": ind.competency.name,
                          "mean": Decimal(sum(vals)) / len(vals)})
    ranked = sorted(items, key=lambda x: (-x["mean"], x["text"]))
    strengths = ranked[:3]
    growth = [x for x in sorted(items, key=lambda x: (x["mean"], x["text"])) if x not in strengths][:3]
    # self vs others, per competency
    me = period.raters.filter(group=A.SELF, status=A.SUBMITTED).first()
    prefix = "CA" if form == "A" else "CB"
    self_by_comp = {}
    if me:
        for r in me.responses.filter(indicator__code__startswith=prefix).select_related("indicator__competency"):
            self_by_comp[r.indicator.competency.name] = r.value
    comps = []
    for comp in sorted({i["competency"] for i in items}, key=lambda c: [x["competency"] for x in items].index(c)):
        row = {"name": comp, "self": self_by_comp.get(comp), "groups": []}
        for key, rows in shown.items():
            vals = list(Response.objects.filter(assignment__in=rows, indicator__competency__name=comp,
                                                indicator__code__in=scoring.scored_codes(form), not_observed=False)
                        .exclude(value=None).values_list("value", flat=True))
            mean = Decimal(sum(vals)) / len(vals) if vals else None
            gap = row["self"] is not None and mean is not None and abs(Decimal(row["self"]) - mean) >= 1
            row["groups"].append({"group": key, "mean": mean, "n": len(rows), "gap": gap})
        comps.append(row)
    comments = Comment.objects.filter(assignment__in=all_rows, status__in=[Comment.RELEASED, Comment.EDITED])
    leader_q = []
    if "Subordinates" in shown:
        for code in ("Q1", "Q2", "Q3"):
            vals = list(Response.objects.filter(assignment__in=shown["Subordinates"], indicator__code=code)
                        .exclude(value=None).values_list("value", flat=True))
            ind = Indicator.objects.get(code=code)
            leader_q.append({"text": ind.wording_1, "mean": Decimal(sum(vals)) / len(vals) if vals else None})
    return {"strengths": strengths, "growth": growth, "competencies": comps, "leader_q": leader_q,
            "comments_strength": [c.released_text for c in comments if c.kind == Comment.STRENGTH],
            "comments_improve": [c.released_text for c in comments if c.kind == Comment.IMPROVE],
            "self_c": {r.indicator.code: r.text for r in me.responses.filter(indicator__code__in=["C2", "C3", "C4"])
                       .select_related("indicator")} if me else {}}


# -- HR's period page ---------------------------------------------------------------------------------

def period_context(acting, period):
    return {"results_template": "performance/_period_results.html",
            "components": period.components.all(), "show": scoring.show, "tpe": scoring.show(period.tpe_total),
            "flagged": period.raters.filter(flag_review="PENDING").select_related("rater"),
            "pending_comments": pending_comments(period).select_related("assignment"),
            "release_problems": release_problems(period), "is_cosp": rules.is_cosp(period.employee),
            "cosp_form": getattr(period, "cosp_form", None)}


def handle_period_post(request, acting, period):
    what, user, POST = request.POST.get("what"), request.user, request.POST
    try:
        if what == "certified":
            encode_certified(acting, user, period, POST.get("rating"), POST.get("ref"))
        elif what == "dtr":
            encode_dtr(acting, user, period, POST.get("absences"), POST.get("tardy"))
        elif what in ("verify_certified", "verify_dtr"):
            verify_input(acting, user, period, what.split("_")[1])
        elif what == "compute":
            r = compute(acting, user, period)
            messages.success(request, f"Computed: TPE {scoring.show(r['total'])}.")
            return
        elif what in ("flag_keep", "flag_void"):
            a = period.raters.get(pk=POST.get("assignment"))
            decide_flag(acting, user, a, what == "flag_keep", POST.get("reason"))
        elif what in ("c_release", "c_edit", "c_withhold"):
            c = Comment.objects.get(pk=POST.get("comment"), assignment__period=period)
            screen_comment(acting, user, c, what[2:], POST.get("text"), POST.get("reason"))
        elif what == "release":
            release(acting, user, period)
        else:
            return
        messages.success(request, "Recorded.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
