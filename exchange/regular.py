"""
Regular Exchange of Duty and the duty schedule (owner decisions 2026-11-03).

Policy: a regular exchange is requested AND approved before the supervisor
submits that month's duty schedule, so the approved swap is part of the
proposed schedule.

  - Kind: decided at filing from the state of the month's schedule for the
    two employees (kind_for):
      * no schedule yet, or only a draft  -> REGULAR (both employees pick
        the shift for their date; filing closes 7 days before the month's
        cut-off date);
      * approved (COH) or recorded        -> MODIFICATION (24 hours' notice);
      * submitted but not yet approved    -> refused (regular filing closed
        when it was submitted; wait for approval, or file as an emergency);
      * "Documented emergency" ticked     -> EMERGENCY (same day allowed).
  - At COH approval a REGULAR exchange is written into the month's draft
    schedule (or into the draft when it is created later): the giver is
    OFF and the taker works the shift. Those days are locked in the draft
    (locked_cells, enforced by schedules.services.save_grid).
  - The supervisor cannot submit the schedule while a regular exchange for
    that month (already consented) is still in the chain; one still waiting
    for the partner's consent is cancelled at submission.
  - EMERGENCY and MODIFICATION never touch a draft; they are written into
    the submitted/approved schedule at COH approval (schedule_checks.py).
"""

from datetime import date, timedelta

from django.db.models import Q

from .models import DutyExchangeRequest as X

CUTOFF_NOTICE_DAYS = 7

STATE_NONE = "none"          # no schedule, or only a draft
STATE_REVIEW = "review"      # submitted / reviewed / recommended / returned
STATE_APPROVED = "approved"  # approved by the COH or recorded


def _month(day):
    return day.year, day.month


def area_schedules(employee, day):
    """Every schedule of this month that covers the employee: one listing
    them, or one for their unit/section."""
    from schedules.models import DutySchedule

    year, month = _month(day)
    return DutySchedule.objects.filter(year=year, month=month).filter(
        Q(rows__employee=employee) | Q(unit__in=employee.units.all())
        | Q(section__in=employee.sections.all(), unit__isnull=True)).distinct()


def month_state(employee, day):
    from schedules.models import DutySchedule as S

    statuses = set(area_schedules(employee, day).values_list("status", flat=True))
    if statuses & {S.APPROVED, S.RECORDED}:
        return STATE_APPROVED
    if statuses & {S.SUBMITTED, S.REVIEWED, S.RECOMMENDED, S.RETURNED}:
        return STATE_REVIEW
    return STATE_NONE


def regular_deadline(employee, day):
    """Last filing day for a regular exchange covering `day`: 7 days before
    the month's cut-off (the draft's own cut-off if there is one)."""
    from schedules.models import DutySchedule as S

    draft = area_schedules(employee, day).filter(status=S.DRAFT).order_by("cutoff_date").first()
    cutoff = draft.cutoff_date if draft else S.default_cutoff(*_month(day))
    return cutoff - timedelta(days=CUTOFF_NOTICE_DAYS), cutoff


def kind_for(employee_a, date_a, employee_b, date_b, is_emergency):
    """(kind or None, [errors])."""
    if is_emergency:
        return X.KIND_EMERGENCY, []
    states = {month_state(e, d) for e in (employee_a, employee_b) for d in (date_a, date_b)}
    if states == {STATE_APPROVED}:
        return X.KIND_MODIFICATION, []
    if states == {STATE_NONE}:
        return X.KIND_REGULAR, []
    if STATE_REVIEW in states:
        return None, ["The duty schedule for one of these dates has already been submitted and is under review. "
                      "Regular exchanges for that month closed when it was submitted. File again once it is "
                      "approved (24 hours' notice), or file now as a documented emergency."]
    return None, ["One date falls in an approved schedule and the other in a month whose schedule is not "
                  "submitted yet. File them as two separate exchanges, or as a documented emergency."]


def regular_errors(employee_a, date_a, employee_b, date_b, shift_a, shift_b, filed_on, exclude=None):
    errors = []
    for label, shift in (("your date", shift_a), ("their date", shift_b)):
        if shift is None:
            errors.append(f"Pick the shift for {label}: the schedule for that month is not prepared yet, so the "
                          "approved exchange is filled into the draft with these shifts.")
        elif not shift.is_duty:
            errors.append(f"The shift for {label} must be a duty shift, not {shift.code}.")
    for day in {date_a, date_b}:
        last, cutoff = regular_deadline(employee_a, day)
        if filed_on > last:
            errors.append(f"Regular exchanges for {day:%B %Y} had to be filed by {last:%b %d, %Y} (7 days before "
                          f"the schedule cut-off on {cutoff:%b %d, %Y}). File it as a documented emergency, or "
                          "after the schedule is approved (24 hours' notice).")
    errors += overlap_errors(employee_a, date_a, employee_b, date_b, exclude)
    return errors


def overlap_errors(employee_a, date_a, employee_b, date_b, exclude=None):
    """No one may be on two live or approved exchanges for the same date."""
    errors = []
    open_or_approved = X.objects.filter(status__in=X.LIVE_STATUSES | {X.APPROVED})
    if exclude is not None:
        open_or_approved = open_or_approved.exclude(pk=exclude.pk)
    for emp in (employee_a, employee_b):
        for day in {date_a, date_b}:
            clash = open_or_approved.filter(
                (Q(employee_a=emp) | Q(employee_b=emp)) & (Q(date_a=day) | Q(date_b=day))).first()
            if clash:
                errors.append(f"{emp} is already on another exchange for {day:%b %d, %Y} "
                              f"(request #{clash.pk}, {clash.get_status_display().lower()}).")
    return errors


# -- the draft schedule ----------------------------------------------------------------

def _moves(x):
    """[(employee, day, shift)] an approved regular exchange puts in the draft."""
    from schedules.services import off_code

    off = off_code()
    return [(x.employee_a, x.date_a, off), (x.employee_b, x.date_a, x.shift_a),
            (x.employee_b, x.date_b, off), (x.employee_a, x.date_b, x.shift_b)]


def _drafts_for(employee, day):
    from schedules.models import DutySchedule as S

    return [s for s in area_schedules(employee, day).filter(status=S.DRAFT)]


def apply_to_drafts(x, user):
    """Write an approved regular exchange into the month's draft schedule(s).
    Nothing to do when no draft exists yet: create_schedule applies it."""
    from schedules.services import add_row, log

    touched = set()
    for emp, day, shift in _moves(x):
        for schedule in _drafts_for(emp, day):
            row = schedule.rows.filter(employee=emp).first() or add_row(schedule, emp)
            row.cells.filter(date=day).update(shift=shift)
            touched.add(schedule)
    for schedule in touched:
        log(schedule, "exchange", user, f"Approved Exchange of Duty #{x.pk} filled in ({x.employee_a} <-> "
                                        f"{x.employee_b}); those days are locked.")
    return touched


def approved_for_schedule(schedule):
    """Approved regular exchanges with a date in this schedule's month that
    involve someone listed on it."""
    first = date(schedule.year, schedule.month, 1)
    last = schedule.days[-1]
    emp_ids = list(schedule.rows.values_list("employee_id", flat=True))
    return (X.objects.filter(kind=X.KIND_REGULAR, status=X.APPROVED)
            .filter(Q(date_a__range=(first, last)) | Q(date_b__range=(first, last)))
            .filter(Q(employee_a_id__in=emp_ids) | Q(employee_b_id__in=emp_ids))
            .select_related("employee_a", "employee_b", "shift_a", "shift_b").order_by("date_a", "pk"))


def locked_cells(schedule):
    """{(employee_id, date): shift_id} set by approved regular exchanges."""
    days = set(schedule.days)
    emp_ids = set(schedule.rows.values_list("employee_id", flat=True))
    out = {}
    for x in approved_for_schedule(schedule):
        for emp, day, shift in _moves(x):
            if day in days and emp.pk in emp_ids:
                out[(emp.pk, day)] = shift.pk if shift else None
    return out


def apply_all_to_new_draft(schedule, user):
    """Called when a draft is created or a row is added: fill in every
    approved regular exchange for its month."""
    from schedules.services import log

    locked = locked_cells(schedule)
    if not locked:
        return 0
    rows = {r.employee_id: r for r in schedule.rows.all()}
    for (emp_id, day), shift_id in locked.items():
        rows[emp_id].cells.filter(date=day).update(shift_id=shift_id)
    log(schedule, "exchange", user, f"{len(locked)} day(s) filled in from approved Exchanges of Duty; locked.")
    return len(locked)


def pending_for_schedule(schedule):
    """Regular exchanges for this schedule's month still in the chain."""
    first, last = date(schedule.year, schedule.month, 1), schedule.days[-1]
    emp_ids = list(schedule.area_employees().values_list("pk", flat=True)) + list(
        schedule.rows.values_list("employee_id", flat=True))
    return (X.objects.filter(kind=X.KIND_REGULAR, status__in=X.LIVE_STATUSES)
            .filter(Q(date_a__range=(first, last)) | Q(date_b__range=(first, last)))
            .filter(Q(employee_a_id__in=emp_ids) | Q(employee_b_id__in=emp_ids))
            .select_related("employee_a", "employee_b"))
