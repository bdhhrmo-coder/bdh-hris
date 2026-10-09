"""
Exchange of Duty checks that need the duty schedules (Batch 5, owner
decisions 2026-10-09). The rules already in rules.py (24-hour notice,
3 per month, consent, routing) stay as they are.

For employee A giving up date A and taking B's date B:
  - A and B must hold the same position.
  - A must be on duty on date A and B on date B, and A must be off on date B
    and B off on date A (no one ends up with two shifts in one day).
  - Timing:
      * the schedule covering a date is already approved (COH): the
        existing 24-hour rule applies;
      * not approved yet: the request must be filed at least 7 days before
        that schedule's cut-off date (set by HR), and is checked against
        the submitted schedule;
      * a documented emergency may still be filed the same day.
  - A date whose section/unit has no submitted schedule on file is not
    checked against a schedule (the request is still allowed, as before
    schedules existed), so exchanges keep working while sections start
    using schedules.
On final (COH) approval the swap is written into the schedule(s), each
change kept in the schedule's history.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError

CUTOFF_NOTICE_DAYS = 7


def _norm(text):
    return " ".join((text or "").split()).lower()


def _on_duty(cell):
    return cell is not None and cell.shift_id is not None and cell.shift.is_duty


def check(employee_a, date_a, employee_b, date_b, filed_on, is_emergency, with_timing=True):
    """Returns (errors, notes)."""
    from schedules.services import cell_for

    errors, notes = [], []
    if not _norm(employee_a.position) or not _norm(employee_b.position):
        errors.append("Both employees' positions must be recorded (ask HR to update the employee record).")
    elif _norm(employee_a.position) != _norm(employee_b.position):
        errors.append(f"Exchange partners must hold the same position ({employee_a.position} vs "
                      f"{employee_b.position}).")

    pairs = [(employee_a, date_a, employee_b, "your"), (employee_b, date_b, employee_a, "their")]
    for who, day, other, _ in pairs:
        schedule, cell = cell_for(who, day)
        if schedule is None:
            notes.append(f"No submitted duty schedule is on file for {who} on {day:%b %d, %Y}; "
                         "that date was not checked against a schedule.")
            continue
        if not _on_duty(cell):
            errors.append(f"{who} is not on duty on {day:%b %d, %Y} in the {schedule.area_name} schedule.")
        _, other_cell = cell_for(other, day)
        if _on_duty(other_cell):
            errors.append(f"{other} already has a duty on {day:%b %d, %Y} - the exchange would give two shifts "
                          "in one day.")
        if with_timing and not is_emergency and schedule.approved_at is None:
            last_day = schedule.cutoff_date - timedelta(days=CUTOFF_NOTICE_DAYS)
            if filed_on > last_day:
                errors.append(
                    f"The {schedule.area_name} schedule for {schedule.month_label} is not approved yet. Requests "
                    f"for that month had to be filed by {last_day:%b %d, %Y} (7 days before its cut-off on "
                    f"{schedule.cutoff_date:%b %d, %Y}). Once the schedule is approved you can file with 24 "
                    "hours' notice, or file now as a documented emergency.")
    return errors, notes


def apply_to_schedules(exchange_request, user):
    """Write an approved exchange into the schedule(s). Raises
    ValidationError if the schedule changed since filing so the swap no
    longer fits (the approver should return the request)."""
    from schedules.services import cell_for, change_cell, off_code

    a, b = exchange_request.employee_a, exchange_request.employee_b
    errors, _ = check(a, exchange_request.date_a, b, exchange_request.date_b, None, True, with_timing=False)
    errors = [e for e in errors if "position" not in e]  # position was checked at filing
    if errors:
        raise ValidationError(errors)
    off = off_code()
    reason = f"Exchange of Duty #{exchange_request.pk} ({a} <-> {b})"
    for giver, taker, day in ((a, b, exchange_request.date_a), (b, a, exchange_request.date_b)):
        schedule, cell = cell_for(giver, day)
        if schedule is None:
            continue
        shift = cell.shift
        change_cell(schedule, giver, day, off, reason, user, exchange=exchange_request)
        change_cell(schedule, taker, day, shift, reason, user, exchange=exchange_request)
