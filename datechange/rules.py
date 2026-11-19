"""
Rules for changing the dates of an approved leave or CTO application
(Part 2, owner decisions 2026-11-19). Everything a view or a test needs to
decide "may this be filed / acted on, and is it valid" lives here.

Owner decisions applied:
  - Only the employee, on their own APPROVED request: COSP leave approved by
    the COH; regular leave once the COH has recommended it ("For Capitol
    approval", or the older "Recorded"); CTO approved by the COH.
  - Only before the request starts: once the first original date has
    arrived it counts as passed. A pending request is cancelled instead.
  - One open change per request. The number of days never changes: the new
    dates must be long enough to hold them, and the approvers check them
    against the schedule (the HRIS has no holiday calendar), as with any
    leave today. New dates stay in the same leave year (calendar year, and
    for COSP Leave also the same contract year), so the same year's credit
    is used and the balance does not move.
  - No overlap with the employee's other pending or approved leave or CTO,
    or with the new dates of their other open date changes.
  - CTO: the usual CTO usage rules (cto.balances.usage_rule_error, the same
    code a new CTO application uses) plus the deadline of the credit already
    held: a change never moves CTO past the deadline of the credit it uses.
  - Same route as the original request; checked again before the last step.
"""

from decimal import ROUND_CEILING, Decimal

from cto.models import CTOUsageApplication
from leave.balances import contract_years_started
from leave.models import LeaveApplication, LeaveType
from leave.permissions import (
    UNIT_STEP,
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    can_endorse,
    can_endorse_second,
    coh_filed_regular,
    supervisor_step_result,
)

from .models import DateChangeRequest as DC

LEAVE_APPROVED = (LeaveApplication.APPROVED, LeaveApplication.FOR_CAPITOL_APPROVAL, LeaveApplication.RECORDED)
_DEAD_LEAVE = (LeaveApplication.REJECTED, LeaveApplication.CANCELLED, LeaveApplication.RETURNED)
_DEAD_CTO = (CTOUsageApplication.REJECTED, CTOUsageApplication.CANCELLED, CTOUsageApplication.RETURNED)


def kind_of(target):
    return DC.KIND_LEAVE if isinstance(target, LeaveApplication) else DC.KIND_CTO


def is_approved(target):
    if isinstance(target, LeaveApplication):
        return target.status in LEAVE_APPROVED
    return target.status == CTOUsageApplication.APPROVED


def open_change(target):
    return target.date_changes.filter(status__in=DC.OPEN_STATUSES).first()


def change_requests_for(target):
    return target.date_changes.prefetch_related("actions__acted_by__employee").order_by("-submitted_at", "-pk")


def date_change_block(acting_employee, target, today):
    """Why this person cannot ask to change the dates of `target` now, or
    None if they can. Used for the button and checked again on filing."""
    if acting_employee is None or acting_employee.pk != target.employee_id:
        return "Only the employee who filed it can ask to change its dates."
    if not is_approved(target):
        return ("Only an approved request can have its dates changed. A pending request is cancelled and "
                "filed again instead.")
    if target.start_date is None:
        return "This application has no inclusive dates to change."
    if today >= target.start_date:
        return "This request has already started or passed, so its dates can no longer be changed."
    if open_change(target) is not None:
        return "A date change for this request is already waiting for approval."
    return None


def _overlaps(start, end, other_start, other_end):
    return other_start is not None and start <= other_end and other_start <= end


def _min_calendar_days(number_of_days):
    return int(Decimal(number_of_days).to_integral_value(rounding=ROUND_CEILING))


def new_dates_error(target, new_start, new_end, today, exclude_change=None):
    """The first reason the new dates are not allowed, or None."""
    if new_start is None or new_end is None:
        return "Enter the new start and end dates."
    if new_end < new_start:
        return "The new end date cannot be before the new start date."
    if new_start <= today:
        return "The new dates must be in the future."
    if (new_start, new_end) == (target.start_date, target.end_date):
        return "The new dates are the same as the approved dates."

    days = target.number_of_days
    if (new_end - new_start).days + 1 < _min_calendar_days(days):
        return (f"These dates are too short for {days.normalize():f} day(s). A date change keeps the same number "
                f"of days; to change the number of days, file a new application instead.")

    employee = target.employee
    # Same leave year: the days come from the same year's credit.
    if new_start.year != target.start_date.year or new_end.year != target.end_date.year:
        return ("The new dates must stay in the same year as the approved dates. For another year, file a new "
                "application instead.")
    if isinstance(target, LeaveApplication):
        lt = target.leave_type
        if lt.balance_tracking == LeaveType.TRACKING_ANNUAL_CAP and lt.is_cumulative:
            year = contract_years_started(employee.date_hired, target.start_date)
            if contract_years_started(employee.date_hired, new_start) != year \
                    or contract_years_started(employee.date_hired, new_end) != year:
                return ("The new dates must stay in the same contract year as the approved dates. For another "
                        "contract year, file a new application instead.")

    # No overlap with other pending or approved leave or CTO.
    leave_qs = employee.leave_applications.exclude(status__in=_DEAD_LEAVE)
    cto_qs = employee.cto_usage_applications.exclude(status__in=_DEAD_CTO)
    if isinstance(target, LeaveApplication):
        leave_qs = leave_qs.exclude(pk=target.pk)
    else:
        cto_qs = cto_qs.exclude(pk=target.pk)
    for other in leave_qs:
        if _overlaps(new_start, new_end, other.start_date, other.end_date):
            return f"The new dates overlap your {other.leave_type.name} ({other.dates_label})."
    for other in cto_qs:
        if _overlaps(new_start, new_end, other.start_date, other.end_date):
            return f"The new dates overlap your CTO ({other.start_date:%b %d, %Y} to {other.end_date:%b %d, %Y})."
    pending = employee.date_changes.filter(status__in=DC.OPEN_STATUSES)
    if exclude_change is not None:
        pending = pending.exclude(pk=exclude_change.pk)
    for other in pending:
        if _overlaps(new_start, new_end, other.new_start_date, other.new_end_date):
            return f"The new dates overlap another date change you asked for ({other.new_dates_label})."

    if isinstance(target, CTOUsageApplication):
        return _cto_error(target, new_start, new_end)
    return None


def _cto_error(application, new_start, new_end):
    """CTO rules, from the same code a new CTO application uses, plus the
    deadline of the credit already held (never extended by a change)."""
    from cto.balances import usage_rule_error
    from cto.separation import is_separating

    employee = application.employee
    error = usage_rule_error(employee, new_start, new_end, application.number_of_days,
                             exclude_application=application)
    if error:
        return error[1]
    if not is_separating(employee):
        for alloc in application.allocations.select_related("line"):
            line = alloc.line
            if line.use_deadline and new_end > line.use_deadline:
                return (f"The CTO credit used for this application must be used by {line.use_deadline:%B %d, %Y}. "
                        f"Choose dates on or before that day.")
            if line.effective_date and new_start < line.effective_date:
                return (f"The CTO credit used for this application counts only from {line.effective_date:%B %d, %Y}. "
                        f"Choose dates on or after that day.")
    return None


# -- routing: the same route as the original request ---------------------------

def _hr_only(change):
    """The COH's own regular leave has no level above: HR alone, as on the original."""
    return change.kind == DC.KIND_LEAVE and coh_filed_regular(change.leave_application)


def next_step(change, acting_employee):
    """(action, resulting status) this person may take now, or None."""
    if acting_employee is None or not change.is_open:
        return None
    filers = [change.employee]
    s = change.status
    if s == DC.SUBMITTED:
        if _hr_only(change):
            return ("process", DC.APPLIED) if can_act_hr_step(acting_employee, filers, change) else None
        if can_endorse(acting_employee, filers):
            if supervisor_step_result(acting_employee, filers) == UNIT_STEP:
                return ("endorse_unit", DC.ENDORSED_BY_UNIT_HEAD)
            return ("endorse", DC.ENDORSED_BY_SUPERVISOR)
        return None
    if s == DC.ENDORSED_BY_UNIT_HEAD:
        return ("endorse", DC.ENDORSED_BY_SUPERVISOR) if can_endorse_second(acting_employee, filers) else None
    if s == DC.ENDORSED_BY_SUPERVISOR:
        return ("process", DC.PROCESSED_BY_HR) if can_act_hr_step(acting_employee, filers, change) else None
    if s == DC.PROCESSED_BY_HR:
        return ("recommend", DC.RECOMMENDED_BY_AO) if can_act_ao_step(acting_employee, filers) else None
    if s == DC.RECOMMENDED_BY_AO and can_act_coh_step(acting_employee, filers):
        return ("recommend_capitol" if change.regular_leave else "approve", DC.APPLIED)
    return None


def may_reject(change):
    """HR never rejects a regular leave (CLAUDE.md §6.2); it may return it."""
    return not (change.regular_leave and change.status == DC.ENDORSED_BY_SUPERVISOR)


def waiting_for(acting_employee, kind):
    """Open date changes of this kind that this person may act on now."""
    if acting_employee is None:
        return []
    qs = DC.objects.filter(kind=kind, status__in=DC.OPEN_STATUSES).select_related(
        "employee", "leave_application__leave_type", "cto_application")
    return [c for c in qs.order_by("submitted_at", "pk") if next_step(c, acting_employee) is not None]


def final_check(change, today):
    """Checked again just before the change takes effect."""
    target = change.target
    if not is_approved(target):
        return "The original request is no longer approved, so its dates cannot be changed. Return this request."
    if (target.start_date, target.end_date) != (change.old_start_date, change.old_end_date):
        return "The original dates have changed since this was filed. Return this request."
    if today >= target.start_date:
        return "The original leave or CTO has already started, so its dates can no longer be changed. Return this request."
    error = new_dates_error(target, change.new_start_date, change.new_end_date, today, exclude_change=change)
    return f"{error} Return this request." if error else None

