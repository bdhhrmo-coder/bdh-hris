"""Who rates whom, and the rating forms (PMS step 3; CLAUDE.md §13C).

The immediate supervisor is the same person the rest of the HRIS sends a
request to first (leave.permissions.endorsers_for); where that runs through
an OIC, the absent approver who designated the OIC rates instead. Nothing
here copies the approver rules."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import RoleAssignment as R
from notifications.services import notify

from . import rules
from .models import (ClinicalSection, EvaluationPeriod as P, Indicator, OrientationAck, RaterAssignment as A,
                     Response, Comment, WordingOverride)

HR_URL = "/performance/periods/"
TASKS_URL = "/performance/tasks/"


# -- people -------------------------------------------------------------------------------

def immediate_supervisors(emp, day=None):
    from leave.permissions import endorsers_for

    day = day or timezone.localdate()
    out = []
    for s in endorsers_for([emp]):
        oic = s.role_assignments.filter(R.in_force_q(day), is_oic=True, role=R.SUPERVISOR).filter(
            Q(unit__in=emp.units.all()) | Q(section__in=emp.sections.all())).exclude(delegated_by=None).first()
        person = oic.delegated_by if oic else s
        if person.pk != emp.pk and person not in out:
            out.append(person)
    return out


def is_unit_head(emp, day=None):
    return emp.role_assignments.filter(R.in_force_q(day), is_oic=False, role=R.SUPERVISOR,
                                       unit__isnull=False).exists()


def form_a_wording(emp):
    override = WordingOverride.objects.filter(employee=emp).first()
    if override:
        return override.track
    clinical = ClinicalSection.objects.filter(section__in=emp.sections.all()).exists()
    return "1" if clinical else "2"


def form_b_wording(emp):
    return "1" if is_unit_head(emp) else "2"


def direct_reports(sup, day=None):
    from employees.models import Employee

    out = []
    for e in Employee.objects.filter(is_active=True, archived_at__isnull=True).exclude(pk=sup.pk):
        if sup in immediate_supervisors(e, day):
            out.append(e)
    return out


def top_management(emp, day=None):
    ao = [e for e in R.objects.filter(R.in_force_q(day), role=R.ADMINISTRATIVE_OFFICER, is_oic=False)
          .select_related("employee")]
    coh = [e for e in R.objects.filter(R.in_force_q(day), role=R.CHIEF_OF_HOSPITAL, is_oic=False)
           .select_related("employee")]
    people = [ra.employee for ra in coh]
    if not emp.role_assignments.filter(role=R.ADMINISTRATIVE_OFFICER, is_oic=False).exists():
        people = [ra.employee for ra in ao] + people
    seen, out = set(), []
    for p in people:
        if p.pk != emp.pk and p.pk not in seen:
            seen.add(p.pk)
            out.append(p)
    return out


def hr_admins(exclude=None):
    from leave.permissions import employees_with_role

    return [e for e in employees_with_role(R.HR_ADMINISTRATOR) if exclude is None or e.pk != exclude.pk]


# -- assignment history (N4; gap 3, 2026-11-26) ------------------------------------------------
#
# Who rates is still decided by the shared approver lookup above. The history
# is the check v1.1 §2 and §7.3 ask for: did the employee work in an area the
# supervisor covered (by the supervisor's own role line) for at least the
# contact share of the period? Subordinates below it are left off the list
# (with a note for HR); a supervisor below it is kept but flagged for HR, who
# may replace them. An opening line ("as of" go-live) means "there since at
# least then", so it counts from the start of the period.

def _days(start, end):
    from datetime import timedelta

    out, day = set(), start
    while day <= end:
        out.add(day)
        day += timedelta(days=1)
    return out


def _covers(role, line):
    if role.role in (R.ADMINISTRATIVE_OFFICER, R.CHIEF_OF_HOSPITAL):
        return True  # hospital-wide
    if role.unit_id:
        return line.unit_id == role.unit_id
    return role.section_id is not None and line.section_id == role.section_id


def history_share(emp, sup, start, end):
    """Share (0 to 1) of [start, end] the verified history puts `emp` in an area
    `sup` covered, or None when `emp` has no assignment history yet."""
    from employees.history import has_lines
    from employees.models import AssignmentLine as L

    if not has_lines(emp):
        return None
    lines = list(L.objects.filter(employee=emp, status=L.VERIFIED, effective_from__lte=end)
                 .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start)))
    roles = list(sup.role_assignments.filter(is_active=True, is_oic=False, role__in=[
        R.SUPERVISOR, R.ADMINISTRATIVE_OFFICER, R.CHIEF_OF_HOSPITAL]))
    covered = set()
    for line in lines:
        l_from = start if line.is_opening else max(start, line.effective_from)
        l_to = min(end, line.effective_to or end)
        for role in roles:
            if not _covers(role, line):
                continue
            r_from, r_to = max(l_from, role.start_date or l_from), min(l_to, role.end_date or l_to)
            if r_from <= r_to:
                covered |= _days(r_from, r_to)
    total = (end - start).days + 1
    return len(covered) / total if total > 0 else 0


def history_check(emp, sup, period):
    """(passes, note) for the contact-share test against the history."""
    share = history_share(emp, sup, period.start_date, period.end_date)
    need = float(rules.num("contact_share", period))
    if share is None:
        return True, "no assignment history yet; the contact question is the check"
    pct = round(share * 100)
    if share + 1e-9 >= need:
        return True, f"history: {pct}% of the period"
    return False, f"history: only {pct}% of the period (needs {round(need * 100)}%); HR to check"


def history_suggestions(period):
    """Former reports of a supervisor-track ratee that the history shows for
    at least the contact share, but who are not on the list (HR may add them)."""
    from employees.models import AssignmentLine as L, Employee

    if period.track != P.SUPERVISOR:
        return []
    sup = period.employee
    on_list = set(period.raters.values_list("rater_id", flat=True))
    roles = list(sup.role_assignments.filter(is_active=True, is_oic=False, role=R.SUPERVISOR))
    ids = set()
    for role in roles:
        qs = L.objects.filter(status=L.VERIFIED, effective_from__lte=period.end_date).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=period.start_date))
        qs = qs.filter(unit=role.unit) if role.unit_id else qs.filter(section=role.section)
        ids |= set(qs.values_list("employee_id", flat=True))
    out = []
    for e in Employee.objects.filter(pk__in=ids - on_list - {sup.pk}, is_active=True):
        if R.objects.filter(employee=e, role=R.SUPERVISOR, is_oic=False, is_active=True).exists():
            continue  # fellow heads are supervisor peers, not subordinates
        ok, note = history_check(e, sup, period)
        if ok and "no assignment history" not in note:
            out.append((e, note))
    return out


# -- building the list ---------------------------------------------------------------------

def due_date(period):
    from employees.holidays import add_working_days

    if period.window_id:
        return period.window.closes_on
    return add_working_days(period.end_date, int(rules.num("cosp_rating_days", period)))


def _add(period, rater, group, form, wording, status=A.ASSIGNED, nominated=False, reason=""):
    a, created = A.objects.get_or_create(period=period, rater=rater, group=group, defaults=dict(
        form=form, wording=wording, status=status, nominated=nominated, due_on=due_date(period),
        added_reason=reason))
    return a


@transaction.atomic
def build_assignments(period, user):
    emp, day = period.employee, period.end_date
    _add(period, emp, A.SELF, "C", "1")
    if period.track == P.RANK:
        for s in immediate_supervisors(emp, day):
            a = _add(period, s, A.SUPERVISOR, "A", form_a_wording(emp))
            _, note = history_check(emp, s, period)
            A.objects.filter(pk=a.pk).update(history_note=note[:255])
    else:
        wording = form_b_wording(emp)
        for t in top_management(emp, day):
            _add(period, t, A.TOP, "B", wording)
        for r in direct_reports(emp, day):
            ok, note = history_check(r, emp, period)
            if not ok:
                rules.log(period, "rater_left_off", user, f"A direct report was left off the subordinate list: {note}")
                continue
            a = _add(period, r, A.SUBORDINATE, "B", wording)
            A.objects.filter(pk=a.pk).update(history_note=note[:255])
    for a in period.raters.filter(status=A.ASSIGNED).exclude(rater=emp).select_related("rater"):
        notify(a.rater, f"Rating task: {period.employee.full_name} ({period.get_period_type_display()}), due "
                        f"{a.due_on:%b %d, %Y}.", TASKS_URL)
    notify(emp, f"Your {period.get_period_type_display().lower()} evaluation has started: please fill in your "
                "self-assessment.", TASKS_URL)


# -- peers ------------------------------------------------------------------------------------

PEER_COUNTED = {A.ASSIGNED, A.IN_PROGRESS, A.SUBMITTED}


def peer_group(period):
    return A.PEER if period.track == P.RANK else A.SUP_PEER


def peer_form(period):
    return "A" if period.track == P.RANK else "B"


def peer_wording(period):
    return form_a_wording(period.employee) if period.track == P.RANK else form_b_wording(period.employee)


def counted_peers(period):
    return period.raters.filter(group__in=[A.PEER, A.POOLED, A.SUP_PEER], status__in=PEER_COUNTED)


def peer_forms_in_window(rater, window, exclude_period=None):
    qs = A.objects.filter(rater=rater, period__window=window, group__in=[A.PEER, A.POOLED, A.SUP_PEER],
                          status__in=PEER_COUNTED)
    if exclude_period is not None:
        qs = qs.exclude(period=exclude_period)
    return qs.count()


def propose_peers(acting, period, people):
    if acting.pk != period.employee_id:
        raise ValidationError("Only the employee proposes their peers.")
    if period.period_type != P.SEMESTRAL or period.status != P.OPEN:
        raise ValidationError("Peers are proposed only in an open semestral window.")
    limit = int(rules.num("peer_proposals", period))
    people = [p for p in people if p.pk != acting.pk]
    if len(people) > limit:
        raise ValidationError(f"Propose at most {limit}.")
    period.raters.filter(group=peer_group(period), status=A.PROPOSED).delete()
    for p in people:
        _add(period, p, peer_group(period), peer_form(period), peer_wording(period), status=A.PROPOSED,
             nominated=True)


def may_finalize(acting, period):
    if acting is None or acting.pk == period.employee_id:
        return False
    return acting in immediate_supervisors(period.employee, period.end_date) or rules.is_hr(acting)


@transaction.atomic
def finalize_peers(acting, user, period, people):
    if not may_finalize(acting, period):
        raise ValidationError("Only the employee's immediate supervisor or HR picks the peers.")
    need = int(rules.num("peers_per_ratee", period)) - counted_peers(period).count()
    if len(people) != need:
        raise ValidationError(f"Pick exactly {need} peer(s).")
    cap = int(rules.num("peer_cap", period))
    proposed = {a.rater_id: a for a in period.raters.filter(group=peer_group(period), status=A.PROPOSED)}
    already = set(period.raters.exclude(status__in=[A.PROPOSED, A.NOT_SELECTED]).values_list("rater_id", flat=True))
    for p in people:
        if p.pk == period.employee_id:
            raise ValidationError("An employee cannot rate themselves.")
        if p.pk in already:
            raise ValidationError(f"{p} is already rating this employee.")
        if peer_forms_in_window(p, period.window, period) >= cap:
            raise ValidationError(f"{p} already has {cap} peer forms in this window.")
    if not counted_peers(period).filter(nominated=False).exists() and all(p.pk in proposed for p in people):
        raise ValidationError("At least one peer must be someone the employee did not propose.")
    for p in people:
        if p.pk in proposed:
            a = proposed.pop(p.pk)
            a.status = A.ASSIGNED
            a.save(update_fields=["status"])
        else:
            a = _add(period, p, peer_group(period), peer_form(period), peer_wording(period), nominated=False)
        notify(p, f"Rating task: a colleague's semestral evaluation, due {a.due_on:%b %d, %Y}.", TASKS_URL)
    for a in proposed.values():
        a.status = A.NOT_SELECTED
        a.save(update_fields=["status"])
    rules.log(period, "peers_finalized", user, f"{len(people)} peer(s) picked")


def peers_to_finalize(acting):
    out = []
    qs = P.objects.filter(period_type=P.SEMESTRAL, status=P.OPEN, window__status="OPEN").select_related(
        "employee", "window")
    for period in qs:
        if acting.pk == period.employee_id or acting not in immediate_supervisors(period.employee, period.end_date):
            continue
        need_default = int(rules.num("peers_per_ratee", period))
        if counted_peers(period).count() < need_default:
            out.append(period)
    return out


@transaction.atomic
def hr_add_rater(acting, user, period, rater, group, reason):
    if not rules.is_hr_admin(acting) or acting.pk == period.employee_id:
        raise ValidationError("Only an HR Administrator (never on their own period) adds raters.")
    if not (reason or "").strip():
        raise ValidationError("Type the reason.")
    if rater.pk == period.employee_id:
        raise ValidationError("An employee cannot be their own rater here.")
    form = "C" if group == A.SELF else ("A" if period.track == P.RANK and group in (A.SUPERVISOR, A.PEER, A.POOLED)
                                        else "B")
    wording = form_a_wording(period.employee) if form == "A" else form_b_wording(period.employee)
    a = _add(period, rater, group, form, wording, reason=reason.strip())
    rules.log(period, "rater_added", user, f"{a.get_group_display()} added: {reason.strip()}")
    notify(rater, f"Rating task: due {a.due_on:%b %d, %Y}.", TASKS_URL)
    return a


def hr_remove_rater(acting, user, assignment, reason):
    if not rules.is_hr_admin(acting) or acting.pk == assignment.period.employee_id:
        raise ValidationError("Only an HR Administrator (never on their own period) removes raters.")
    if not (reason or "").strip():
        raise ValidationError("Type the reason.")
    assignment.status = A.VOID
    assignment.flag_note = reason.strip()[:255]
    assignment.save(update_fields=["status", "flag_note"])
    rules.log(assignment.period, "rater_removed", user, f"{assignment.get_group_display()} removed: {reason.strip()}")


# -- rating ----------------------------------------------------------------------------------

def asks_contact(a):
    return a.group in (A.PEER, A.POOLED, A.SUP_PEER, A.SUBORDINATE)


def asks_conflict(a):
    return a.group != A.SELF


def share_words(period):
    share = rules.num("contact_share", period)
    return "half" if share == rules.Decimal("0.5") else f"{round(share * 100)}%"


def contact_question(a):
    name, share = a.period.employee.full_name, share_words(a.period)
    if a.group == A.SUBORDINATE:
        return f"In this period, did you report to {name} for at least {share} of the time?"
    return f"In this period, did you work directly with {name} for at least {share} of the time, at least weekly?"


def needs_orientation(a):
    qs = OrientationAck.objects.filter(employee=a.rater)
    qs = qs.filter(window=a.period.window) if a.period.window_id else qs.filter(period=a.period)
    return not qs.exists()


def acknowledge(a):
    OrientationAck.objects.create(employee=a.rater, window=a.period.window if a.period.window_id else None,
                                  period=None if a.period.window_id else a.period)


def _closed_uncounted(a, status, user, why):
    a.status = status
    a.save(update_fields=["status"])
    rules.log(a.period, "form_closed", user, f"A {a.get_group_display().lower()} form was closed: {why}")
    notify(hr_admins(exclude=a.period.employee),
           f"A rating form for {a.period.employee.full_name} was closed without counting ({why}). Please check.",
           f"/performance/periods/{a.period_id}/")
    if a.group in (A.PEER, A.SUP_PEER, A.POOLED):
        notify(immediate_supervisors(a.period.employee, a.period.end_date),
               f"Pick a replacement peer for {a.period.employee.full_name}.", "/performance/tasks/")


def answer_contact(a, yes, user):
    if a.status not in A.LIVE:
        raise ValidationError("This form is closed.")
    a.contact_ok = bool(yes)
    a.save(update_fields=["contact_ok"])
    if not yes:
        _closed_uncounted(a, A.DECLINED, user, "no working contact")


def answer_conflict(a, applies, user):
    if a.status not in A.LIVE:
        raise ValidationError("This form is closed.")
    if applies:
        _closed_uncounted(a, A.CONFLICT, user, "a conflict was declared")
    else:
        a.conflict_cleared = True
        a.save(update_fields=["conflict_cleared"])


def items_for(a):
    """[(competency, [indicators])] for this form."""
    if a.form == "C":
        prefix = "CA" if a.period.track == P.RANK else "CB"
        codes = Q(code__startswith=prefix) | Q(code__in=["C2", "C3", "C4"])
    elif a.form == "A":
        codes = Q(code__regex=r"^A\d+$")
    else:
        codes = Q(code__regex=r"^B\d+$")
        if a.group == A.SUBORDINATE:
            codes |= Q(code__in=["Q1", "Q2", "Q3"])
    groups = {}
    for ind in Indicator.objects.filter(codes).select_related("competency").order_by("sort_order"):
        groups.setdefault(ind.competency, []).append(ind)
    return list(groups.items())


def _value(raw, allow_no):
    raw = (raw or "").strip()
    if raw == "NO" and allow_no:
        return None, True
    if raw in {"1", "2", "3", "4", "5"}:
        return int(raw), False
    return None, None


@transaction.atomic
def save_answers(a, data, user, submit=False):
    if a.status not in A.LIVE:
        raise ValidationError("This form is locked.")
    if a.form != "C" and asks_contact(a) and a.contact_ok is not True:
        raise ValidationError("Answer the working-contact question first.")
    missing = []
    for comp, inds in items_for(a):
        for ind in inds:
            key = f"i_{ind.code}"
            if ind.is_text:
                text = (data.get(key) or "").strip()
                Response.objects.update_or_create(assignment=a, indicator=ind, defaults={"text": text})
                if ind.required and not text:
                    missing.append(ind.code)
                continue
            value, no = _value(data.get(key), allow_no=a.form != "C")
            if no is None:
                Response.objects.filter(assignment=a, indicator=ind).delete()
                missing.append(ind.code)
                continue
            Response.objects.update_or_create(assignment=a, indicator=ind,
                                              defaults={"value": value, "not_observed": no})
    if a.form in ("A", "B"):
        for kind, key in ((Comment.STRENGTH, "c_strength"), (Comment.IMPROVE, "c_improve")):
            text = (data.get(key) or "").strip()
            a.comments.filter(kind=kind, status=Comment.PENDING).delete()
            if text:
                Comment.objects.create(assignment=a, kind=kind, raw_text=text)
    a.status = A.IN_PROGRESS
    if submit:
        if missing:
            raise ValidationError(f"Answer every required item before submitting ({len(missing)} left).")
        a.status, a.submitted_at = A.SUBMITTED, timezone.now()
    a.save(update_fields=["status", "submitted_at"])
    return missing


def tasks_for(acting):
    return (A.objects.filter(rater=acting, status__in=A.LIVE)
            .exclude(period__status__in=[P.CANCELLED, P.PLANNED])
            .select_related("period__employee", "period__window").order_by("due_on"))


# -- pooling help (decision 2, 2026-11-26, Option A: HR adds pooled peers by hand) --------------

def short_of_peers(period):
    """How many more valid peers a semestral period needs (0 when it has enough)."""
    if period.period_type != P.SEMESTRAL or period.status in (P.PLANNED, P.CANCELLED):
        return 0
    need = int(rules.num("peers_per_ratee", period))
    return max(0, need - counted_peers(period).count())


def pool_candidates(period, limit=8):
    """Suggested pooled peers from the nearest larger group: the rest of the
    employee's section (v1.1 §2). Empty for a section with no one free; HR
    then picks from a section that works with it regularly."""
    from employees.models import Employee

    taken = set(period.raters.values_list("rater_id", flat=True)) | {period.employee_id}
    cap = int(rules.num("peer_cap", period))
    out = []
    for e in (Employee.objects.filter(is_active=True, archived_at__isnull=True,
                                      sections__in=period.employee.sections.all())
              .exclude(pk__in=taken).distinct().order_by("surname", "first_name")):
        if period.window_id and peer_forms_in_window(e, period.window) >= cap:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def needs_pooling(period):
    """Short of peers after the supervisor picked them (a peer declined or had a
    conflict), or still short when the window closed."""
    short = short_of_peers(period)
    if not short:
        return 0
    closed = period.window_id and period.window.status == "CLOSED"
    picked = period.events.filter(action="peers_finalized").exists()
    return short if (closed or picked) else 0
