"""Deductions (PMS step 5; CLAUDE.md §13C, rebuilt 2026-11-26).

Route: anyone files with evidence (never about themselves) -> another HR
Processor/Administrator validates -> the employee explains within the
explanation window -> the AO approves or dismisses (the COH when the AO is the
employee or the filer) -> the employee may ask for a review within the review
window: HR review -> AO endorses (an HR Administrator when the AO approved
it) -> the COH decides -> otherwise Final when the window closes.
Only Final deductions dated inside a period count; per-criterion caps first,
then the period cap (scoring.py)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment as R
from employees.holidays import add_working_days
from notifications.services import notify

from . import rules
from .models import CriterionChange, Deduction as D, DeductionCriterion, DeductionEvent, EvaluationPeriod as P

URL = "/performance/incidents/"


def _log(d, action, user, note=""):
    DeductionEvent.objects.create(deduction=d, action=action, note=(note or "")[:500], user=user)


def _ao_list(exclude):
    from leave.permissions import employees_with_role

    ids = {e.pk for e in exclude}
    aos = [e for e in employees_with_role(R.ADMINISTRATIVE_OFFICER) if e.pk not in ids]
    if aos:
        return aos
    return [e for e in employees_with_role(R.CHIEF_OF_HOSPITAL) if e.pk not in ids]


def decider_ok(acting, d):
    """The AO approves; the COH when the AO is the employee or the filer."""
    ao_involved = any(p.has_role(R.ADMINISTRATIVE_OFFICER) for p in (d.employee, d.filed_by))
    if ao_involved:
        return rules.is_coh(acting) and acting.pk not in (d.employee_id, d.filed_by_id)
    return rules.is_ao(acting) and acting.pk not in (d.employee_id, d.filed_by_id)


def usable_criteria(employee):
    track = rules.track_for(employee, timezone.localdate())
    qs = DeductionCriterion.objects.filter(is_active=True).exclude(points_options="")
    if track != P.SUPERVISOR:
        qs = qs.exclude(applies_to=DeductionCriterion.SUPERVISOR)
    return qs


@transaction.atomic
def file(acting, user, employee, criterion, incident_date, description, points, evidence_ref="", final_ref="",
         auto_source="", today=None):
    if acting is not None and acting.pk == employee.pk:
        raise ValidationError("You cannot file a deduction about yourself.")
    if not criterion.is_active or not criterion.points_options:
        raise ValidationError("That criterion is not in use.")
    if criterion.applies_to == DeductionCriterion.SUPERVISOR and \
            rules.track_for(employee, incident_date) != P.SUPERVISOR:
        raise ValidationError("That criterion applies to supervisors only.")
    points = Decimal(str(points))
    if points not in criterion.options():
        raise ValidationError(f"Points must be one of {criterion.points_options}.")
    if not (description or "").strip():
        raise ValidationError("Describe what happened.")
    if not auto_source and not (evidence_ref or "").strip():
        raise ValidationError("Give the evidence (a reference; files can be attached after filing).")
    if criterion.needs_final_ref and not (final_ref or "").strip():
        raise ValidationError("This criterion needs the final decision's reference.")
    if incident_date > (today or timezone.localdate()):
        raise ValidationError("The incident date cannot be in the future.")
    d = D.objects.create(employee=employee, criterion=criterion, incident_date=incident_date,
                         description=description.strip(), evidence_ref=(evidence_ref or "").strip(),
                         final_decision_ref=(final_ref or "").strip(), points=points,
                         filed_by=acting if acting is not None else employee, auto_source=auto_source)
    _log(d, "file", user, auto_source)
    from leave.permissions import hr_employees

    notify([h for h in hr_employees() if h.pk not in (employee.pk, d.filed_by_id)],
           f"A performance incident was filed about {employee.full_name}: please validate.", URL)
    return d


@transaction.atomic
def validate(acting, user, d, approve, note="", points=None):
    if d.status != D.FILED:
        raise ValidationError("Not waiting for validation.")
    if not rules.is_hr(acting) or acting.pk in (d.employee_id, d.filed_by_id):
        raise ValidationError("Another HR person validates (never the filer or the employee).")
    if not approve:
        if not (note or "").strip():
            raise ValidationError("Type the reason for dismissing it.")
        d.status = D.DISMISSED
        d.save(update_fields=["status"])
        _log(d, "dismiss_hr", user, note)
        notify([d.employee, d.filed_by], f"The performance incident about {d.employee.full_name} was dismissed at "
                                         "validation.", URL)
        return d
    if points is not None and Decimal(str(points)) != d.points:
        points = Decimal(str(points))
        if points not in d.criterion.options():
            raise ValidationError(f"Points must be one of {d.criterion.points_options}.")
        if not (note or "").strip():
            raise ValidationError("Type the reason for changing the points.")
        d.points = points
    d.status = D.VALIDATED
    d.explanation_due = add_working_days(timezone.localdate(), int(rules.number_for("explanation_days")))
    d.save(update_fields=["status", "points", "explanation_due"])
    _log(d, "validate", user, note)
    notify(d.employee, f"A performance incident was filed about you ({d.criterion}). You may explain by "
                       f"{d.explanation_due:%b %d, %Y}.", URL)
    return d


def explain(acting, user, d, text):
    if acting.pk != d.employee_id:
        raise ValidationError("Only the employee explains.")
    if d.status != D.VALIDATED:
        raise ValidationError("Not waiting for your explanation.")
    if not (text or "").strip():
        raise ValidationError("Type your explanation.")
    d.explanation = text.strip()
    d.save(update_fields=["explanation"])
    _log(d, "explain", user)
    notify(_ao_list([d.employee, d.filed_by]), f"An explanation was given on a performance incident about "
                                               f"{d.employee.full_name}.", URL)


@transaction.atomic
def decide(acting, user, d, approve, note=""):
    if d.status != D.VALIDATED:
        raise ValidationError("Not waiting for a decision.")
    if not decider_ok(acting, d):
        raise ValidationError("The AO decides (the COH when the AO is the employee or the filer).")
    if not d.explanation and timezone.localdate() <= d.explanation_due:
        raise ValidationError("Wait for the explanation, or until the explanation window closes.")
    if not approve and not (note or "").strip():
        raise ValidationError("Type the reason for dismissing it.")
    if approve:
        d.status = D.APPROVED
        d.review_due = add_working_days(timezone.localdate(), int(rules.number_for("deduction_review_days")))
    else:
        d.status = D.DISMISSED
    d.save(update_fields=["status", "review_due"])
    _log(d, "approve" if approve else "dismiss", user, note)
    notify(d.employee, f"The performance incident ({d.criterion}) was {'approved' if approve else 'dismissed'}."
                       + (f" You may ask for a review by {d.review_due:%b %d, %Y}." if approve else ""), URL)


def request_review(acting, user, d, grounds):
    if acting.pk != d.employee_id:
        raise ValidationError("Only the employee asks for a review.")
    if d.status != D.APPROVED or timezone.localdate() > d.review_due:
        raise ValidationError("The review window is closed.")
    if not (grounds or "").strip():
        raise ValidationError("Give the grounds.")
    d.status, d.review_grounds = D.REVIEW_HR, grounds.strip()
    d.save(update_fields=["status", "review_grounds"])
    _log(d, "review_request", user, grounds)


def _approved_by_ao(d):
    ev = d.events.filter(action="approve").last()
    emp = getattr(ev.user, "employee", None) if ev else None
    return emp is not None and emp.has_role(R.ADMINISTRATIVE_OFFICER)


@transaction.atomic
def review_step(acting, user, d, note="", stands=True):
    parties = (d.employee_id, d.filed_by_id)
    if acting.pk in parties:
        raise ValidationError("Not on an incident you are part of.")
    if not (note or "").strip():
        raise ValidationError("Type a note.")
    if d.status == D.REVIEW_HR:
        if not rules.is_hr(acting):
            raise ValidationError("HR reviews first.")
        d.status = D.REVIEW_AO
        action = "review_hr"
    elif d.status == D.REVIEW_AO:
        ok = rules.is_hr_admin(acting) if _approved_by_ao(d) else rules.is_ao(acting)
        if not ok:
            raise ValidationError("The AO endorses (an HR Administrator when the AO approved it).")
        d.status = D.REVIEW_COH
        action = "review_endorse"
    elif d.status == D.REVIEW_COH:
        if not rules.is_coh(acting):
            raise ValidationError("The COH decides.")
        action = "review_decide"
        if stands:
            _make_final(d, user)
        else:
            d.status = D.DISMISSED
    else:
        raise ValidationError("Not under review.")
    d.save(update_fields=["status", "final_at"])
    _log(d, action, user, ("stands: " if stands else "dismissed: ") + note if action == "review_decide" else note)


def _make_final(d, user=None):
    d.status, d.final_at = D.FINAL, timezone.now()
    # A computed-but-unreleased period containing it is computed again.
    for p in P.objects.filter(employee=d.employee, start_date__lte=d.incident_date, end_date__gte=d.incident_date,
                              status__in=[P.COMPUTED, P.UNDER_REVIEW]):
        p.status = P.OPEN
        p.save(update_fields=["status"])
        rules.log(p, "reopened", user, "A deduction became final; compute again.")


def finalize_due(today=None):
    today = today or timezone.localdate()
    n = 0
    for d in D.objects.filter(status=D.APPROVED, review_due__lt=today):
        _make_final(d)
        d.save(update_fields=["status", "final_at"])
        _log(d, "final", None, "review window closed")
        n += 1
    return n


def period_deductions(period):
    """(rows, total) of Final deductions dated inside the period. Per-criterion
    caps first; the period cap is applied by the caller."""
    qs = D.objects.filter(employee=period.employee, status=D.FINAL, incident_date__gte=period.start_date,
                          incident_date__lte=period.end_date).select_related("criterion").order_by("incident_date")
    if period.released_at:
        qs = qs.filter(final_at__lte=period.released_at)
    rows, per_crit = [], {}
    for d in qs:
        if d.criterion.applies_to == DeductionCriterion.SUPERVISOR and period.track != P.SUPERVISOR:
            continue
        used = per_crit.get(d.criterion_id, Decimal("0"))
        pts = d.points
        capped = False
        if d.criterion.cap is not None and used + pts > d.criterion.cap:
            pts, capped = max(Decimal("0"), d.criterion.cap - used), True
        per_crit[d.criterion_id] = used + pts
        rows.append({"d": d, "points": pts, "capped": capped})
    return rows, sum((r["points"] for r in rows), Decimal("0"))


def late_notes(period):
    """Deductions that became Final after the period's release (notes only)."""
    if not period.released_at:
        return []
    return list(D.objects.filter(employee=period.employee, status=D.FINAL, incident_date__gte=period.start_date,
                                 incident_date__lte=period.end_date, final_at__gt=period.released_at))


def waiting_for(acting):
    out = []
    qs = D.objects.exclude(status__in=[D.FINAL, D.DISMISSED, D.APPROVED]).select_related("employee", "criterion",
                                                                                         "filed_by")
    for d in qs:
        if acting.pk in (d.employee_id, d.filed_by_id):
            if d.status == D.VALIDATED and acting.pk == d.employee_id and not d.explanation:
                out.append((d, "Explain"))
            continue
        if d.status == D.FILED and rules.is_hr(acting):
            out.append((d, "Validate"))
        elif d.status == D.VALIDATED and decider_ok(acting, d):
            out.append((d, "Decide"))
        elif d.status == D.REVIEW_HR and rules.is_hr(acting):
            out.append((d, "Review (HR)"))
        elif d.status == D.REVIEW_AO and (rules.is_hr_admin(acting) if _approved_by_ao(d) else rules.is_ao(acting)):
            out.append((d, "Endorse review"))
        elif d.status == D.REVIEW_COH and rules.is_coh(acting):
            out.append((d, "Decide review"))
    return out


def change_criterion(acting, user, criterion, points_options, cap, is_active, approval_ref):
    from decimal import InvalidOperation

    if not rules.is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator changes the criteria.")
    if not (approval_ref or "").strip():
        raise ValidationError("Type the PMT/COH approval reference.")
    try:
        cleaned = ",".join(f"{Decimal(x.strip()):.2f}" for x in points_options.split(",") if x.strip())
        cap = Decimal(cap) if str(cap).strip() else None
    except InvalidOperation:
        raise ValidationError("Points and cap must be numbers.")
    for field, new in (("points_options", cleaned), ("cap", cap), ("is_active", bool(is_active))):
        old = getattr(criterion, field)
        if str(old) != str(new):
            CriterionChange.objects.create(criterion=criterion, field=field, old_value=str(old), new_value=str(new),
                                           approval_ref=approval_ref.strip(), changed_by=user)
            setattr(criterion, field, new)
    criterion.approval_ref = approval_ref.strip()
    criterion.save()
