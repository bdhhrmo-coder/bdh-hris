"""
Out-on-Pass rules checker (CLAUDE.md section 13B).

The system checks every rule when a pass is filed. A pass that breaks a rule
is not processed: check_pass() returns the reasons and the filing screen
shows them to the employee. Nothing here changes data.

All amounts are whole minutes. The policy numbers are read from
settings.OUT_ON_PASS_RULES so they are not fixed in code.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone


def rule(name):
    return settings.OUT_ON_PASS_RULES[name]


# -- Time arithmetic ---------------------------------------------------------

def _aware(d, t):
    return timezone.make_aware(datetime.combine(d, t))


def window(pass_date, time_out, time_in):
    """(start, end) as aware datetimes; a time in earlier than the time out
    means the employee comes back the next day (night shift)."""
    start = _aware(pass_date, time_out)
    end = _aware(pass_date, time_in)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def planned_minutes(time_out, time_in):
    start, end = window(date(2000, 1, 3), time_out, time_in)
    return int((end - start).total_seconds() // 60)


def charged_minutes(tapped_out_at, tapped_in_at):
    """(minutes charged, excess minutes) from the Tap Out / Tap In times.

    Actual minutes, no rounding: seconds on both taps are ignored, so
    10:05:59 out and 10:47:10 in charge 42 minutes. Over the 2-hour limit by
    not more than the grace minutes: not excess, but still charged."""
    out = tapped_out_at.replace(second=0, microsecond=0)
    back = tapped_in_at.replace(second=0, microsecond=0)
    minutes = max(0, int((back - out).total_seconds() // 60))
    over = minutes - rule("MAX_MINUTES_PER_PASS")
    excess = over if over > rule("GRACE_MINUTES") else 0
    return minutes, excess


# -- Allowance and balance ---------------------------------------------------

def _full_months(start, end):
    """Whole months from start to end (both inclusive): Jan 1-Jun 30 = 6."""
    after = end + timedelta(days=1)
    months = (after.year - start.year) * 12 + after.month - start.month
    if after.day < start.day:
        months -= 1
    return max(0, months)


def allowance_minutes(employee, year):
    """Permanent (Regular) staff: 12 hours a year. COSP: 1 hour per full
    month of contract inside the year, up to 12 hours, from the contract
    periods HR entered (none entered = no allowance yet). Contracts that
    started before the year count from Jan 1 of the year."""
    if employee.employment_status != employee.EMPLOYMENT_STATUS_COSP:
        return rule("REGULAR_HOURS_PER_YEAR") * 60
    first, last = date(year, 1, 1), date(year, 12, 31)
    months = 0
    for period in employee.cosp_contract_periods.filter(start_date__lte=last, end_date__gte=first):
        months += _full_months(max(period.start_date, first), min(period.end_date, last))
    hours = min(months * rule("COSP_HOURS_PER_MONTH"), rule("COSP_MAX_HOURS_PER_YEAR"))
    return hours * 60


def _year_passes(employee, year, exclude_pk=None):
    from .models import OutOnPass

    qs = employee.out_on_passes.filter(pass_date__year=year)
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


def used_minutes(employee, year, exclude_pk=None):
    """Minutes already charged: the Jan 1-10 opening line plus completed passes."""
    from .models import OutOnPass

    opening = employee.pass_opening_usage.filter(year=year).aggregate(s=Sum("minutes_used"))["s"] or 0
    charged = (_year_passes(employee, year, exclude_pk).filter(status=OutOnPass.COMPLETED)
               .aggregate(s=Sum("minutes_charged"))["s"] or 0)
    return opening + charged


def held_minutes(employee, year, exclude_pk=None):
    """Planned minutes of passes still in the chain or approved but not yet
    back: held aside so two pending passes cannot spend the same balance."""
    from .models import OutOnPass

    live = _year_passes(employee, year, exclude_pk).filter(status__in=OutOnPass.LIVE_STATUSES)
    return sum(p.planned_minutes for p in live)


def balance_minutes(employee, year, exclude_pk=None):
    """Allowance less used and held minutes (may be below zero after an
    approved exception)."""
    return (allowance_minutes(employee, year) - used_minutes(employee, year, exclude_pk)
            - held_minutes(employee, year, exclude_pk))


def passes_in_month(employee, year, month, exclude_pk=None):
    """Passes that count toward the monthly limits: live and completed ones,
    plus any January passes recorded on the opening line."""
    from .models import OutOnPass

    count = (_year_passes(employee, year, exclude_pk)
             .filter(pass_date__month=month,
                     status__in=OutOnPass.LIVE_STATUSES + (OutOnPass.COMPLETED,)).count())
    if month == 1:
        count += sum(employee.pass_opening_usage.filter(year=year).values_list("passes_used_in_month", flat=True))
    return count


# -- Shift and unit checks ---------------------------------------------------

def scheduled_shifts(employee, pass_date):
    """(start, end) of the duty shifts on an APPROVED or RECORDED schedule that
    start on the pass date or the day before (a night shift running past
    midnight). Empty when the employee has no approved schedule."""
    from schedules.models import DutySchedule, ScheduleCell

    cells = (ScheduleCell.objects
             .filter(row__employee=employee, date__in=[pass_date - timedelta(days=1), pass_date],
                     row__schedule__status__in=DutySchedule.LOCKED,
                     shift__is_duty=True, shift__start_time__isnull=False, shift__end_time__isnull=False)
             .select_related("shift"))
    out = []
    for c in cells:
        out.append(window(c.date, c.shift.start_time, c.shift.end_time))
    return out


def has_approved_schedule(employee, pass_date):
    from schedules.models import DutySchedule

    return employee.schedule_rows.filter(
        schedule__year=pass_date.year, schedule__month=pass_date.month,
        schedule__status__in=DutySchedule.LOCKED).exists()


def _work_area_q(employee):
    """Same unit; for an employee with no unit, the same section."""
    unit_ids = list(employee.units.values_list("id", flat=True))
    if unit_ids:
        return Q(employee__units__in=unit_ids)
    return Q(employee__sections__in=list(employee.sections.values_list("id", flat=True)))


def others_out_at_once(employee, start, end, exclude_pk=None):
    """Other employees of the same unit with a live pass overlapping start-end."""
    from .models import OutOnPass

    qs = (OutOnPass.objects.filter(_work_area_q(employee), status__in=OutOnPass.LIVE_STATUSES,
                                   pass_date__in=[start.date() - timedelta(days=1), start.date()])
          .exclude(employee=employee).distinct())
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    names = set()
    for p in qs.select_related("employee"):
        p_start, p_end = window(p.pass_date, p.time_out, p.time_in)
        if p_start < end and start < p_end:
            names.add(p.employee_id)
    return len(names)


# -- The checker -------------------------------------------------------------

@dataclass
class Violation:
    code: str
    message: str


def _hm(minutes):
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    return f"{sign}{minutes // 60} h {minutes % 60:02d} min"


def check_pass(employee, kind, pass_date, time_out, time_in, *, now=None, has_proof=False,
               exclude_pk=None):
    """Every rule a pass must meet when it is filed (or resubmitted).

    Returns a list of Violation; an empty list means the pass may be
    processed. `exclude_pk` leaves out the pass itself when it is resubmitted.

    Each special type lifts only its own rule (assumption, to confirm with
    the owner): a third pass lifts the "2 passes a month" limit; an
    exception lifts the balance and 2-hour limits; an emergency pass may be
    filed after the fact. Every other rule still applies to them.
    """
    from .models import OutOnPass

    now = now or timezone.now()
    found = []

    def refuse(code, message):
        found.append(Violation(code, message))

    start, end = window(pass_date, time_out, time_in)
    minutes = int((end - start).total_seconds() // 60)

    # No filing after the fact, except a documented emergency pass.
    if kind != OutOnPass.EMERGENCY and start <= now:
        refuse("after_the_fact",
               "A pass must be filed and approved before you leave. If you already had to leave "
               "for a documented emergency, file it as an emergency pass.")
    if kind == OutOnPass.EMERGENCY and start > now:
        refuse("emergency_future",
               "An emergency pass is filed after you have left. For a planned pass, file a personal pass.")

    # Length and balance (an exception lifts these two).
    if minutes <= 0:
        refuse("no_time", "The expected time back must be after the time out.")
    if kind != OutOnPass.EXCEPTION:
        if minutes > rule("MAX_MINUTES_PER_PASS"):
            refuse("over_2_hours",
                   f"A pass may not be longer than {_hm(rule('MAX_MINUTES_PER_PASS'))}; this one is {_hm(minutes)}. "
                   "File leave, or ask for an exception if the matter is urgent and cannot be handled by leave.")
        balance = balance_minutes(employee, pass_date.year, exclude_pk)
        if minutes > balance:
            refuse("balance",
                   f"Your remaining balance is {_hm(max(balance, 0))}; this pass needs {_hm(minutes)}. "
                   "File leave, or ask for an exception if the matter is urgent and cannot be handled by leave.")
    elif minutes <= rule("MAX_MINUTES_PER_PASS") and minutes <= balance_minutes(employee, pass_date.year, exclude_pk):
        refuse("exception_not_needed",
               "This pass is within your balance and the 2-hour limit. File it as a personal pass.")

    # One pass a day.
    same_day = (_year_passes(employee, pass_date.year, exclude_pk)
                .filter(pass_date=pass_date, status__in=OutOnPass.LIVE_STATUSES + (OutOnPass.COMPLETED,)))
    if same_day.exists():
        refuse("one_a_day", "You already have a pass on this day. Only one pass a day is allowed.")

    # Monthly limits.
    count = passes_in_month(employee, pass_date.year, pass_date.month, exclude_pk)
    if count >= rule("MAX_PASSES_PER_MONTH"):
        refuse("max_month",
               f"You already have {count} passes this month, the most allowed. Please file leave instead.")
    elif kind == OutOnPass.THIRD:
        if count < rule("PASSES_PER_MONTH"):
            refuse("third_not_needed",
                   f"You have {count} pass(es) this month, so this is not a third pass. File a personal pass.")
        if not has_proof:
            refuse("third_proof", "Attach the proof of the urgent reason. A third pass without proof is not processed.")
    elif count >= rule("PASSES_PER_MONTH"):
        refuse("third_pass",
               f"You already have {count} passes this month. A third pass is allowed only for an urgent "
               "reason: file it as a third pass with the proof attached.")

    # First and last hour of the shift (when an approved schedule shows it).
    margin = timedelta(minutes=rule("SHIFT_EDGE_MINUTES"))
    for s_start, s_end in scheduled_shifts(employee, pass_date):
        if start < s_start + margin and s_start < end:
            refuse("first_hour", f"No pass in the first hour of your shift (starts {s_start:%I:%M %p}).")
        if end > s_end - margin and start < s_end:
            refuse("last_hour", f"No pass in the last hour of your shift (ends {s_end:%I:%M %p}).")

    # Unit limit.
    others = others_out_at_once(employee, start, end, exclude_pk)
    if others >= rule("MAX_OUT_PER_UNIT"):
        refuse("unit_limit",
               f"{others} other employees of your unit already have a pass at that time. "
               f"At most {rule('MAX_OUT_PER_UNIT')} may be out at once. Choose another time.")

    return found


def needs_shift_confirmation(employee, pass_date):
    """True when the system cannot check the shift-hour rule (no approved
    schedule for the month), so the supervisor confirms it at endorsement."""
    return not has_approved_schedule(employee, pass_date)


def summary(employee, on_date):
    """Balance figures for the filing page, the pass page and (later) the slip.

    Same accounting as the filing check (balance_minutes), only broken down:
      used      = charged minutes of completed passes + the Jan 1-10 opening line
      pending   = planned minutes of passes waiting for endorsement/approval
      committed = planned minutes of approved (or recorded) passes not yet back
      available = allowance - used - pending - committed  (what can still be filed)
    Cancelled, rejected and returned passes hold nothing.
    """
    from .models import OutOnPass

    year = on_date.year
    allowance = allowance_minutes(employee, year)
    used = used_minutes(employee, year)
    live = list(_year_passes(employee, year).filter(status__in=OutOnPass.LIVE_STATUSES))
    pending = sum(p.planned_minutes for p in live if p.status in OutOnPass.PENDING_STATUSES)
    committed = sum(p.planned_minutes for p in live if p.status in OutOnPass.CLEARED_STATUSES)
    return {
        "year": year, "allowance": allowance, "used": used,
        "pending": pending, "committed": committed, "held": pending + committed,
        "before_holds": allowance - used,
        "balance": allowance - used - pending - committed,
        "month_count": passes_in_month(employee, year, on_date.month),
        "month_label": on_date.strftime("%B %Y"),
        "max_minutes": rule("MAX_MINUTES_PER_PASS"),
        "passes_per_month": rule("PASSES_PER_MONTH"),
        "max_passes_per_month": rule("MAX_PASSES_PER_MONTH"),
    }


def _pct(part, whole):
    return max(0, min(100, round(part * 100 / whole))) if whole > 0 else 0


def balance_panel(s):
    """The standard balance summary (templates/components/balance_summary.html)
    for Out-on-Pass, from summary()."""
    allowance, used, pending, committed = s["allowance"], s["used"], s["pending"], s["committed"]
    available = s["balance"]
    used_pct, pend_pct, comm_pct = _pct(used, allowance), _pct(pending, allowance), _pct(committed, allowance)
    free_pct = max(0, 100 - used_pct - pend_pct - comm_pct) if available > 0 else 0
    spent_pct = 100 - free_pct if allowance else 0
    if allowance <= 0:
        sub = f"No allowance yet for {s['year']}. COSP: HR enters your contract period."
    else:
        sub = f"Entitlement year {s['year']}  ·  {spent_pct}% used or on hold  ·  {free_pct}% remaining"
    count, std, most = s["month_count"], s["passes_per_month"], s["max_passes_per_month"]
    if count < std:
        state, label = "ok", "Within the monthly limit"
    elif count == std:
        state, label = "full", "Monthly limit used"
    else:
        state, label = "over", "Third pass used: no more passes this month"
    steps = [{"state": "used" if i < count else "free"} for i in range(std)]
    steps.append({"state": "extra-used" if count > std else "extra"})
    left = max(0, std - count)
    return {
        "primary": {
            "label": "Available balance", "value": hm(max(available, 0)) if available >= 0 else hm(available),
            "sub": sub, "state": "ok" if available > 0 else "out",
            "segments": [
                {"kind": "used", "pct": used_pct, "label": "Used"},
                {"kind": "pending", "pct": pend_pct, "label": "Pending approval"},
                {"kind": "committed", "pct": comm_pct, "label": "Approved, not yet used"},
                {"kind": "free", "pct": free_pct, "label": "Available"},
            ],
            "legend": [
                {"kind": "used", "label": "Used", "value": hm(used)},
                {"kind": "pending", "label": "Pending approval", "value": hm(pending)},
                {"kind": "committed", "label": "Approved, not yet used", "value": hm(committed)},
            ],
        },
        "cards": [
            {"label": "Total allowance", "value": hm(allowance), "tone": "navy", "note": f"Year {s['year']}"},
            {"label": "Used", "value": hm(used), "tone": "neutral",
             "note": "Actual minutes from Tap Out to Tap In"},
            {"label": "On hold / committed", "value": hm(pending + committed), "tone": "amber",
             "note": f"Pending {hm(pending)}  ·  Approved {hm(committed)}"},
        ],
        "limit": {
            "title": f"Passes in {s['month_label']}", "state": state,
            "value": f"{count} of {std} passes" if count <= std else f"{count} passes (incl. third pass)",
            "steps": steps,
            "sub": f"{label}. {left} standard pass{'es' if left != 1 else ''} left; never more than {most} a month.",
            "note": "A third pass is allowed only for an urgent reason, with supporting documentation, "
                    "decided by the Chief of Hospital.",
        },
        "footnote": f"Balance before pending and approved passes: {hm(s['before_holds'])}. Cancelled, rejected and "
                    "returned passes are not deducted.",
    }


def hm(minutes):
    """'1 h 05 min' for screens and the slip."""
    return _hm(minutes)
