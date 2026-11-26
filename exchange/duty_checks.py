"""
Exchange of Duty: equal length and the duties next to a swapped date
(owner decisions 2026-11-26). Used for requests filed under the new rules.

  - Equal length: both duties must have the same paid hours. An exchange is
    hour for hour and never creates OT, CTO or pay (CLAUDE.md §8); coverage
    of a longer duty is filed as OT instead.
  - For each employee, the duty they take over is compared with their
    nearest duty before it and after it (the duty they give up is removed
    first). Only those two pairs are judged, so a problem already elsewhere
    in the schedule never blocks a swap:
      * straight duty: the next duty starts at or before the previous one
        ends (no gap, or an overlap) - blocked;
      * PM to AM: a duty starting at or after noon, then a duty starting
        before noon on the next calendar date - blocked, even when the rest
        hours are met (each duty counts on the date it starts);
      * rest: the gap is shorter than the rest set for the length of the duty
        just finished (8 h -> 8, 12 h -> 12, any other length -> that many
        hours) - blocked.
  - Warning only: more duty days in a row than the employee's own pattern
    (Employee.shift_hours: 12 -> 3, 8 -> 5; HR settings).
Duties are read from every schedule the employee is on, drafts included,
so the check works before a schedule is submitted (D6, Option A).
"""

from datetime import datetime, timedelta
from decimal import Decimal

WINDOW_DAYS = 3


def rules():
    from .models import ExchangeRules

    return ExchangeRules.get()


def _rank(status):
    from schedules.models import DutySchedule as S

    order = [S.DRAFT, S.RETURNED, S.SUBMITTED, S.REVIEWED, S.RECOMMENDED, S.APPROVED, S.RECORDED]
    return order.index(status) if status in order else -1


def duties(employee, start, end):
    """{date: ShiftCode} of the employee's duty days from start to end, from
    every schedule (drafts included). A day with a duty in any schedule is a
    duty day; the most advanced schedule wins when there are several."""
    from schedules.models import ScheduleCell

    best = {}
    cells = (ScheduleCell.objects.filter(row__employee=employee, date__range=(start, end), shift__is_duty=True)
             .select_related("shift", "row__schedule"))
    for c in cells:
        rank = _rank(c.row.schedule.status)
        if c.date not in best or rank > best[c.date][0]:
            best[c.date] = (rank, c.shift)
    return {d: v[1] for d, v in best.items()}


class Duty:
    def __init__(self, day, shift):
        self.day, self.shift = day, shift
        self.start = datetime.combine(day, shift.start_time)
        end = datetime.combine(day, shift.end_time)
        if end <= self.start:
            end += timedelta(days=1)
        self.end = end

    @property
    def hours(self):
        return Decimal(self.shift.paid_hours)

    def label(self):
        return f"{self.shift.code} on {self.day:%a %b %d}"


def _timed(shift):
    return shift is not None and shift.start_time is not None and shift.end_time is not None


def rest_needed(hours, r):
    hours = Decimal(hours)
    if hours == 8:
        return Decimal(r.rest_after_8)
    if hours == 12:
        return Decimal(r.rest_after_12)
    return hours


def pair_problem(first, second, r):
    """(rule, text) when `second` may not follow `first`, else None."""
    if second.start <= first.end:
        return "straight duty", "no gap between the two duties" if second.start == first.end else \
            "the two duties overlap"
    if first.shift.start_time >= r.pm_from and second.shift.start_time < r.pm_from \
            and second.day == first.day + timedelta(days=1):
        return "PM to AM", "a PM duty followed by an AM duty the next day"
    gap = Decimal((second.start - first.end).total_seconds()) / 3600
    need = rest_needed(first.hours, r)
    if gap < need:
        return "rest", f"{gap.normalize():f} hours of rest, {need.normalize():f} needed after a " \
                       f"{first.hours.normalize():f}-hour duty"
    return None


def length_error(shift_a, shift_b):
    if shift_a is None or shift_b is None:
        return None
    if Decimal(shift_a.paid_hours) == Decimal(shift_b.paid_hours):
        return None
    return (f"These duties are different lengths ({shift_a.code} = {Decimal(shift_a.paid_hours).normalize():f} h, "
            f"{shift_b.code} = {Decimal(shift_b.paid_hours).normalize():f} h). An exchange must be hour for hour. "
            "For coverage of a longer duty, ask your Chief Nurse or HR to file OT.")


def check_taker(employee, give_day, take_day, take_shift, r=None):
    """(errors, warnings) for `employee` giving up `give_day` and working
    `take_shift` on `take_day`."""
    r = r or rules()
    errors, warnings = [], []
    if not _timed(take_shift):
        return errors, warnings
    around = duties(employee, take_day - timedelta(days=WINDOW_DAYS + 7), take_day + timedelta(days=WINDOW_DAYS + 7))
    around.pop(give_day, None)
    # A duty already on take_day stays in: it shows up as an overlap or
    # straight duty with the one taken over.
    new = Duty(take_day, take_shift)
    timed = sorted((Duty(d, s) for d, s in around.items() if _timed(s)), key=lambda x: x.start)
    before = [d for d in timed if d.start <= new.start]
    after = [d for d in timed if d.start > new.start]
    for first, second in ((before[-1] if before else None, new), (new, after[0] if after else None)):
        if first is None or second is None:
            continue
        problem = pair_problem(first, second, r)
        if problem:
            rule, why = problem
            errors.append(f"Blocked ({rule}): {employee} would work {first.label()} and then {second.label()} "
                          f"- {why}.")
    # Duty days in a row, counted from the day the swapped duty starts.
    days = set(around) | {take_day}
    run, d = 1, take_day - timedelta(days=1)
    while d in days:
        run, d = run + 1, d - timedelta(days=1)
    d = take_day + timedelta(days=1)
    while d in days:
        run, d = run + 1, d + timedelta(days=1)
    limit = r.consecutive_12 if employee.shift_hours == 12 else r.consecutive_8
    if run > limit:
        warnings.append(f"{employee} would have {run} duty days in a row (their {employee.shift_hours}-hour pattern "
                        f"normally has at most {limit}). Allowed; please check before endorsing.")
    return errors, warnings


def check_swap(employee_a, date_a, shift_a, employee_b, date_b, shift_b):
    """(errors, warnings, notes). shift_a = A's duty on date A (B takes it);
    shift_b = B's duty on date B (A takes it)."""
    errors, warnings, notes = [], [], []
    msg = length_error(shift_a, shift_b)
    if msg:
        errors.append(msg)
    if shift_a is None or shift_b is None:
        notes.append("The duty times could not be read for both dates, so rest between duties was not checked.")
        return errors, warnings, notes
    r = rules()
    for taker, give_day, take_day, take_shift in ((employee_a, date_a, date_b, shift_b),
                                                  (employee_b, date_b, date_a, shift_a)):
        e, w = check_taker(taker, give_day, take_day, take_shift, r)
        errors += e
        warnings += w
    return errors, warnings, notes


def flags_for_schedule(schedule):
    """Rest / straight duty / PM-to-AM problems next to days set by approved
    regular exchanges, at schedule submission (flagged, not blocked; D6 A)."""
    from .regular import locked_cells
    from schedules.models import ShiftCode

    r = rules()
    shifts = {s.pk: s for s in ShiftCode.objects.all()}
    out = []
    from employees.models import Employee

    for (emp_id, day), shift_id in locked_cells(schedule).items():
        shift = shifts.get(shift_id)
        if shift is None or not shift.is_duty or not _timed(shift):
            continue
        emp = Employee.objects.get(pk=emp_id)
        around = duties(emp, day - timedelta(days=WINDOW_DAYS), day + timedelta(days=WINDOW_DAYS))
        around.pop(day, None)
        new = Duty(day, shift)
        timed = sorted((Duty(d, s) for d, s in around.items() if _timed(s)), key=lambda x: x.start)
        before = [d for d in timed if d.start < new.start]
        after = [d for d in timed if d.start > new.start]
        for first, second in ((before[-1] if before else None, new), (new, after[0] if after else None)):
            if first and second:
                problem = pair_problem(first, second, r)
                if problem:
                    out.append(f"{emp}: {first.label()} then {second.label()} ({problem[0]}) next to an approved "
                               "Exchange of Duty.")
    return out
