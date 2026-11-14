"""
Stage 3: Tap Out / Tap In and missed taps (CLAUDE.md 13B; DICR Rev 1).

The employee taps Out and In on a hospital computer; the HRIS time is the
official gate time. The guard on duty keeps a cross-check log (name and pass
number) and can see the day's passes on the Gate page.

Charging happens on Tap In (or when HRMO reviews a missed tap that completes
the pair):
  minutes charged  = actual whole minutes, seconds ignored, no rounding
  excess minutes   = minutes over 2 hours, beyond the 5-minute grace
  over balance     = charged minutes the balance did not cover
Excess and over-balance minutes are time not covered (undertime for
permanent staff, hours not rendered for COSP/PGSS). They are recorded and
listed for HR; nothing is deducted from leave credits here.

A missed tap: the employee gives the time and reason -> the supervisor
confirms -> the guard on duty verifies (and may correct the time) -> HRMO
reviews, and only then is the time recorded and the pass charged. Any step
may reject it with a remark; the employee may then file a corrected one.
The guards are the PGSS security personnel ("COSP under" = PGSS).
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment
from employees.models import Employee
from leave.permissions import ao_step_actors, endorsers_for, hr_employees

from . import rules
from .models import MissedTap as M
from .models import MissedTapAction
from .models import OutOnPass as P
from .models import OutOnPassAction


def current_time():
    """The gate clock (one place, so tests can set it)."""
    return timezone.now()


def _ids(people):
    return {e.pk for e in people}


# -- Taps -------------------------------------------------------------------

def tap_out_window(p):
    start, end = rules.window(p.pass_date, p.time_out, p.time_in)
    return start - timedelta(minutes=rules.rule("TAP_OUT_EARLY_MINUTES")), end


def _pending_missed(p, which):
    return p.missed_taps.filter(which=which, status__in=M.PENDING_STATUSES).exists()


def tap_out_problem(acting, p, now=None):
    """Why this person can't Tap Out now, or None if they can."""
    now = now or current_time()
    if acting is None or acting.pk != p.employee_id:
        return "Only the employee on the pass can tap."
    if p.status not in P.CLEARED_STATUSES:
        return "The pass is not approved."
    if p.tapped_out_at:
        return "You already tapped out."
    if p.kind == P.EMERGENCY:
        return "An emergency pass is filed after you left: record the time out as a missed Tap Out."
    if _pending_missed(p, M.OUT):
        return "A missed Tap Out is already being reviewed."
    opens, closes = tap_out_window(p)
    if now < opens:
        return f"Tap Out opens at {timezone.localtime(opens):%I:%M %p} on {timezone.localtime(opens):%b %d}."
    if now > closes:
        return "The expected time in has passed, so this pass can no longer be used. Cancel it, or report a missed Tap Out."
    return None


def tap_in_problem(acting, p):
    if acting is None or acting.pk != p.employee_id:
        return "Only the employee on the pass can tap."
    if p.status not in P.CLEARED_STATUSES:
        return "The pass is not open."
    if not p.tapped_out_at:
        return "Tap Out first (or report a missed Tap Out)."
    if p.tapped_in_at:
        return "You already tapped in."
    if _pending_missed(p, M.IN):
        return "A missed Tap In is already being reviewed."
    return None


def complete(p, user, note=""):
    """Charge the pass from its two gate times and close it."""
    minutes, excess = rules.charged_minutes(p.tapped_out_at, p.tapped_in_at)
    available = rules.balance_minutes(p.employee, p.pass_date.year, exclude_pk=p.pk)
    p.minutes_charged = minutes
    p.excess_minutes = excess
    p.over_balance_minutes = max(0, minutes - max(available, 0))
    p.status = P.COMPLETED
    p.completed_at = current_time()
    p.save()
    OutOnPassAction.objects.create(out_on_pass=p, action="complete", resulting_status=P.COMPLETED,
                                   notes=note or f"{rules.hm(minutes)} charged", acted_by=user)


@transaction.atomic
def tap_out(p, user, now=None):
    p.tapped_out_at = now or current_time()
    p.save(update_fields=["tapped_out_at"])
    OutOnPassAction.objects.create(out_on_pass=p, action="tap_out", resulting_status=p.status, acted_by=user,
                                   notes=f"Tapped out {timezone.localtime(p.tapped_out_at):%I:%M %p}")


@transaction.atomic
def tap_in(p, user, now=None):
    p.tapped_in_at = now or current_time()
    p.save(update_fields=["tapped_in_at"])
    OutOnPassAction.objects.create(out_on_pass=p, action="tap_in", resulting_status=p.status, acted_by=user,
                                   notes=f"Tapped in {timezone.localtime(p.tapped_in_at):%I:%M %p}")
    complete(p, user)


# -- Missed taps ------------------------------------------------------------

def missed_tap_problem(acting, p, which, now=None):
    now = now or current_time()
    if acting is None or acting.pk != p.employee_id:
        return "Only the employee on the pass can report a missed tap."
    if p.status not in P.CLEARED_STATUSES:
        return "The pass is not open."
    if _pending_missed(p, which):
        return "One is already being reviewed."
    if which == M.OUT:
        if p.tapped_out_at:
            return "You already tapped out."
        if now < tap_out_window(p)[0] and p.kind != P.EMERGENCY:
            return "The pass has not started yet."
    else:
        if p.tapped_in_at:
            return "You already tapped in."
        if not p.tapped_out_at and not _pending_missed(p, M.OUT):
            return "Report the missed Tap Out first."
    return None


def claimed_time_problem(p, which, claimed, now=None):
    now = now or current_time()
    if claimed > now:
        return "The time cannot be in the future."
    start, end = rules.window(p.pass_date, p.time_out, p.time_in)
    if claimed < start - timedelta(hours=12) or claimed > end + timedelta(hours=24):
        return "The time must be on the day of the pass."
    if which == M.IN and p.tapped_out_at and claimed <= p.tapped_out_at:
        return "The time in must be after the time out."
    if which == M.OUT and p.tapped_in_at and claimed >= p.tapped_in_at:
        return "The time out must be before the time in."
    return None


def guards():
    """The guards on duty: active PGSS security personnel."""
    return list(Employee.objects.filter(is_active=True, archived_at__isnull=True,
                                        employment_status=Employee.EMPLOYMENT_STATUS_COSP,
                                        cosp_under=Employee.COSP_UNDER_PGSS))


def is_guard(acting):
    return (acting is not None and acting.employment_status == Employee.EMPLOYMENT_STATUS_COSP
            and acting.cosp_under == Employee.COSP_UNDER_PGSS)


def _acted_ids(m):
    return {a.acted_by_id for a in m.actions.all()}


def missed_step_actors(m):
    """Who acts on a missed tap now. Nobody acts on their own, and nobody acts twice."""
    emp = m.out_on_pass.employee
    done_users = _acted_ids(m)
    if m.status == M.SUBMITTED:
        people = endorsers_for([emp]) or ao_step_actors([emp])  # the COH's own: the AO confirms
    elif m.status == M.CONFIRMED:
        people = guards()
    elif m.status == M.VERIFIED:
        people = hr_employees()
    else:
        return []
    return [e for e in people if e.pk != emp.pk and e.user_id not in done_users]


def can_act_missed(acting, m):
    return acting is not None and acting.pk in _ids(missed_step_actors(m))


NEXT = {M.SUBMITTED: (M.CONFIRMED, "confirm", "Confirm"),
        M.CONFIRMED: (M.VERIFIED, "verify", "Verify"),
        M.VERIFIED: (M.REVIEWED, "review", "Review and record")}


@transaction.atomic
def act_on_missed(m, user, action, notes="", verified_at=None):
    """action: the forward action for the current step, or 'reject'."""
    if action == "reject":
        m.status = M.REJECTED
    else:
        new_status, name, _ = NEXT[m.status]
        if name != action:
            raise ValueError("Wrong step.")
        if new_status == M.VERIFIED and verified_at is not None:
            m.verified_at = verified_at
        m.status = new_status
    m.save()
    MissedTapAction.objects.create(missed_tap=m, action=action, resulting_status=m.status, notes=notes,
                                   acted_by=user)
    if m.status == M.REVIEWED:
        p = P.objects.select_for_update().get(pk=m.out_on_pass_id)
        when = m.time_to_record
        if m.which == M.OUT:
            p.tapped_out_at = when
        else:
            p.tapped_in_at = when
        p.save(update_fields=["tapped_out_at", "tapped_in_at"])
        OutOnPassAction.objects.create(
            out_on_pass=p, action=f"missed_tap_{m.which.lower()}", resulting_status=p.status, acted_by=user,
            notes=f"{m.get_which_display()} {timezone.localtime(when):%I:%M %p} recorded after HRMO review")
        if p.tapped_out_at and p.tapped_in_at and p.status in P.CLEARED_STATUSES:
            complete(p, user, note="Charged after a missed-tap review")
    return m


def missed_waiting_for(acting):
    """Missed taps waiting for this person (supervisor and HRMO steps; guards use the Gate page)."""
    if acting is None:
        return []
    pending = M.objects.filter(status__in=M.PENDING_STATUSES).select_related("out_on_pass__employee")
    return [m for m in pending if can_act_missed(acting, m)]


def can_view_gate(acting):
    if acting is None:
        return False
    return is_guard(acting) or any(acting.has_role(r, viewing=True) for r in (
        RoleAssignment.HR_PROCESSOR, RoleAssignment.HR_ADMINISTRATOR,
        RoleAssignment.ADMINISTRATIVE_OFFICER, RoleAssignment.CHIEF_OF_HOSPITAL))


def gate_passes(today=None):
    """Today's approved passes (and last night's still out), for the Gate page."""
    today = today or timezone.localdate()
    qs = (P.objects.filter(pass_date__in=[today - timedelta(days=1), today])
          .filter(status__in=P.CLEARED_STATUSES + (P.COMPLETED,))
          .select_related("employee").order_by("pass_date", "time_out"))
    return [p for p in qs if p.pass_date == today or (p.tapped_out_at and not p.tapped_in_at)]


def not_back(today=None):
    """Passes tapped out but never tapped in after their expected time in: for HR's follow-up."""
    now = current_time()
    out = P.objects.filter(status__in=P.CLEARED_STATUSES, tapped_out_at__isnull=False, tapped_in_at__isnull=True)
    return [p for p in out.select_related("employee")
            if rules.window(p.pass_date, p.time_out, p.time_in)[1] < now and not _pending_missed(p, M.IN)]
