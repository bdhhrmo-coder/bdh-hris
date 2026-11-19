"""
CTO can cover part or all of a COSP leave (Part 3, owner decisions
2026-11-19).

  - COSP employees only, on their COSP-route leave (COSP Leave, Wellness,
    Emergency), chosen when the leave is filed (or corrected before anyone
    acts / after a return). Default: leave credits only.
  - The CTO days are one date range inside the leave's dates, with a number
    of days (half days allowed, as for CTO applications).
  - One application, one route: the normal COSP leave route. The CTO days
    are held in a CTOUsageApplication linked to the leave (`cosp_leave`) that
    is never routed on its own; it simply follows the leave's status. So the
    existing CTO code applies to those days without a second copy: the credit
    is picked and held by plan_allocation (effective date arrived, not past
    its deadline, oldest first), and usage_rule_error checks the 5-days-a-
    month and no-3-consecutive-days rules. Days charged to leave credits are
    not subject to the CTO limits.
  - Balances: CTO is held at filing and counted at COH approval; leave
    credits are deducted for the rest only. Both are checked at filing and
    again at COH approval; if the CTO credit is no longer good then, the
    leave is returned with the reason.
  - Return, rejection or cancellation releases the CTO days.
"""

from decimal import ROUND_CEILING, Decimal

from django.db import transaction
from django.utils import timezone

HALF = Decimal("0.5")


def cover_of(application):
    """The linked CTO record, or None."""
    from cto.models import CTOUsageApplication

    if application is None or application.pk is None:
        return None
    return CTOUsageApplication.objects.filter(cosp_leave=application).first()


def cto_days_of(application):
    cover = cover_of(application)
    return cover.number_of_days if cover else Decimal("0")


def leave_credit_days(application):
    """Days charged to leave credits: the rest of the leave."""
    return application.number_of_days - cto_days_of(application)


def may_use_cto(employee, leave_type):
    """COSP employees, on leave that follows the COSP route."""
    from cto.permissions import is_cto_eligible

    from .models import LeaveApplication

    return (employee is not None and employee.employment_status == "COSP" and leave_type is not None
            and LeaveApplication.route_for(employee, leave_type) == LeaveApplication.ROUTE_COSP
            and is_cto_eligible(employee))


def shape_error(number_of_days, start, end, cto_days, cto_start, cto_end):
    """Checks that need no database: the CTO days fit inside the leave."""
    if not cto_days or cto_days <= 0:
        return "Enter how many of the leave days are charged to CTO."
    if cto_days % HALF != 0:
        return "CTO is charged in half days (0.5) or whole days."
    if number_of_days is not None and cto_days > number_of_days:
        return "The CTO days cannot be more than the leave days."
    if not cto_start or not cto_end:
        return "Enter the first and last date charged to CTO."
    if cto_end < cto_start:
        return "The last CTO date cannot be before the first."
    if start and end and (cto_start < start or cto_end > end):
        return "The CTO dates must be inside the leave dates."
    if (cto_end - cto_start).days + 1 < int(cto_days.to_integral_value(rounding=ROUND_CEILING)):
        return f"The CTO dates are too short for {cto_days.normalize():f} CTO day(s)."
    return None


def check_and_plan(employee, application, leave_type, number_of_days, start, cto_days, cto_start, cto_end,
                   today=None):
    """(error, plan). Runs the CTO rules on the CTO days and checks both
    balances. `application` is the leave being corrected (or None when new)."""
    from cto.balances import plan_allocation, usage_rule_error

    from .balances import compute_available_balance

    today = today or timezone.localdate()
    existing = cover_of(application)
    rule = usage_rule_error(employee, cto_start, cto_end, cto_days, exclude_application=existing)
    if rule:
        return f"CTO days: {rule[1]}", None
    # Call inside transaction.atomic(): the leave's own held days are given
    # back before planning again, and put back if anything fails.
    savepoint = transaction.savepoint()
    if existing is not None:
        existing.allocations.all().delete()
    plan = plan_allocation(employee, cto_days, today, cto_end)
    error = None
    if plan is None:
        error = ("Not enough CTO credit for these days. CTO counts from its effective date and must be used by its "
                 "deadline (normally December 15).")
    else:
        rest = number_of_days - cto_days
        if rest > 0:
            available = compute_available_balance(employee, leave_type, as_of_date=start)
            if available is not None and rest > available:
                error = (f"Not enough {leave_type.name} credits for the other {rest.normalize():f} day(s): "
                         f"{available} available.")
    if error:
        transaction.savepoint_rollback(savepoint)
        return error, None
    transaction.savepoint_commit(savepoint)
    return None, plan


def attach(application, cto_days, cto_start, cto_end, plan):
    """Creates or refreshes the linked CTO record and holds its credit."""
    from cto.models import CTOCreditAllocation, CTOUsageApplication

    cover = cover_of(application) or CTOUsageApplication(employee=application.employee, cosp_leave=application)
    cover.start_date, cover.end_date, cover.number_of_days = cto_start, cto_end, cto_days
    cover.reason = f"Covers part of COSP leave LV-{application.pk:06d}"
    cover.status = CTOUsageApplication.SUBMITTED
    cover.save()
    cover.allocations.all().delete()
    CTOCreditAllocation.objects.bulk_create(
        CTOCreditAllocation(line=line, usage_application=cover, days=d) for line, d in plan)
    return cover


def detach(application):
    """The employee chose leave credits only: the CTO days are released."""
    cover = cover_of(application)
    if cover is not None:
        cover.delete()  # its allocations go with it; the leave's own log keeps the history


def follow_leave(application):
    """Keeps the linked CTO record in step with the leave: approved with it;
    released when the leave is returned, rejected or cancelled."""
    from cto.models import CTOUsageApplication as C

    from .models import LeaveApplication as L

    cover = cover_of(application)
    if cover is None:
        return
    mirror = {L.APPROVED: C.APPROVED, L.RETURNED: C.RETURNED, L.REJECTED: C.REJECTED,
              L.CANCELLED: C.CANCELLED}.get(application.status)
    if mirror is None:
        return
    if mirror != C.APPROVED:
        cover.allocations.all().delete()
    cover.status = mirror
    cover.save(update_fields=["status", "updated_at"])


def error_at_approval(application, today=None):
    """Checked again just before the COH approves: the CTO credit held must
    still be good for the CTO days. Returns the reason, or None."""
    from cto.models import CTOClaimLine
    from cto.separation import is_separating

    cover = cover_of(application)
    if cover is None:
        return None
    today = today or timezone.localdate()
    held = sum((a.days for a in cover.allocations.all()), Decimal("0"))
    if held < cover.number_of_days:
        return "the CTO credit for these days is no longer held"
    if is_separating(application.employee):
        return None
    for alloc in cover.allocations.select_related("line"):
        line = alloc.line
        if line.state != CTOClaimLine.CREDITED:
            return f"the CTO credit from work on {line.work_date:%B %d, %Y} is no longer available ({line.get_state_display().lower()})"
        if line.use_deadline and cover.end_date > line.use_deadline:
            return f"the CTO credit from work on {line.work_date:%B %d, %Y} expired on {line.use_deadline:%B %d, %Y}"
    return None


def split_note(application):
    """One line for the leave's log and the Audit Log."""
    cover = cover_of(application)
    if cover is None:
        return f"All {application.number_of_days.normalize():f} day(s) charged to leave credits."
    rest = application.number_of_days - cover.number_of_days
    dates = (f"{cover.start_date:%b %d, %Y}" if cover.start_date == cover.end_date
             else f"{cover.start_date:%b %d} to {cover.end_date:%b %d, %Y}")
    return (f"{cover.number_of_days.normalize():f} day(s) charged to CTO ({dates}); "
            f"{rest.normalize():f} day(s) to leave credits.")
