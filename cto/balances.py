"""
CTO compliance math, isolated from views/forms for the same reason
leave/balances.py is: CLAUDE.md §7 flags this as the highest
compliance-risk module in the build and specifically asks for it to be
unit-tested in isolation.

Confirmed with the project owner on 2026-09-27 (not guessed):
  - Conversion: credited_hours = hours_worked * multiplier (1.0 weekday /
    1.5 rest day or holiday, from the active CTOMultiplierRate).
  - Day length is per employee, by shift: 8-hour-shift staff use CTO in
    4-hour (half-day) or 8-hour (full-day) blocks; 12-hour-shift staff use
    6-hour (half) or 12-hour (full) blocks. Both normalize to the same
    "day" unit (0.5 or 1.0), so credited_days = credited_hours /
    employee.shift_hours, and every usage application's number_of_days
    must be a multiple of 0.5 regardless of shift length.
  - The "max 5 CTO days/month, no 3+ consecutive days" cap in §7 is a
    USAGE cap (confirmed), not an earning cap.

Documented interpretation, not explicitly stated in CLAUDE.md (flagged to
the project owner alongside this phase, same as the leave-accrual
anchor-date question was):
  - "No 3+ consecutive days" is checked against the employee's full CTO
    usage history, not just within a single application — two separate
    2-day applications filed back-to-back would still form an illegal
    4-day run, so the adjacency check looks at CTO usage immediately
    before/after the requested range, not just the range itself.
  - "CTO usage dates cannot extend past Dec 15" is read as: no CTO usage
    application's end_date may fall after December 15 of ITS OWN year,
    for any year (a standing year-end cutoff, not tied to which year the
    credit was earned).
  - Forfeiture (see cto/management/commands/forfeit_expired_cto.py) sweeps
    an employee's entire remaining CTO balance at each year-end, on the
    assumption that the sweep runs every year without a gap — under that
    assumption a whole-balance sweep and a strict "only forfeit what was
    earned in year Y" calculation give the same result, because nothing
    from before year Y should still be on the books by the time year Y's
    sweep runs. A Chief-of-Hospital-approved exception is handled by
    simply not running (or partially running) that sweep for the
    employee it covers, with the reason recorded in the ledger entry's
    notes — see the management command for the exact mechanics.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

MONTHLY_USAGE_CAP_DAYS = Decimal("5")
MAX_CONSECUTIVE_DAYS = 2  # "no 3+ consecutive days" -> at most 2 in a row
USAGE_CUTOFF_MONTH_DAY = (12, 15)  # December 15
FILING_DEADLINE_MONTH_DAY = (11, 30)  # November 30


def compute_credit(hours_worked, is_restday_or_holiday, multiplier_rate, shift_hours):
    """
    Returns (multiplier_applied, credited_hours, credited_days) for one
    CTOCreditEntry. multiplier_rate is a CTOMultiplierRate instance (the
    one active on the work date — callers look that up so the choice of
    "which rate applies" stays visible and testable on its own).

    Both results are quantized to 2 decimal places (matching
    CTOCreditEntry.credited_hours/credited_days) — a plain Decimal
    multiply/divide of two 2-decimal-place values (e.g. 8.00 hours * 1.00
    multiplier) otherwise produces more decimal places than the model
    field allows (8.0000), which fails full_clean() on every ordinary
    credit entry. Rounding here, at the single point both numbers are
    computed, keeps the same quantize/ROUND_HALF_UP convention already
    used in attendance/deductions.py rather than each caller re-rounding.
    """
    multiplier = (
        multiplier_rate.restday_holiday_multiplier if is_restday_or_holiday else multiplier_rate.weekday_multiplier
    )
    credited_hours = (hours_worked * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    credited_days = (credited_hours / Decimal(shift_hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return multiplier, credited_hours, credited_days


def _ledger_total(employee, start_date=None, end_date=None):
    qs = employee.cto_credit_transactions.all()
    if start_date is not None:
        qs = qs.filter(transaction_date__gte=start_date)
    if end_date is not None:
        qs = qs.filter(transaction_date__lte=end_date)
    return qs.aggregate(total=Sum("days"))["total"] or Decimal("0")


def compute_available_cto_balance(employee, as_of_date=None):
    """Current CTO balance: the running sum of the ledger, full stop."""
    as_of_date = as_of_date or date.today()
    return _ledger_total(employee, end_date=as_of_date)


def cto_used_in_month(employee, year, month, exclude_application=None):
    """
    Sum of number_of_days for this employee's CTO usage applications whose
    start_date falls in the given month, counting every application that
    hasn't been rejected/cancelled (a pending or approved request both
    hold a claim against the monthly cap — only a dead application drops
    out of it).
    """
    from .models import CTOUsageApplication

    qs = employee.cto_usage_applications.filter(
        start_date__year=year, start_date__month=month,
    ).exclude(status__in=[CTOUsageApplication.REJECTED, CTOUsageApplication.CANCELLED])
    if exclude_application is not None:
        qs = qs.exclude(pk=exclude_application.pk)
    return qs.aggregate(total=Sum("number_of_days"))["total"] or Decimal("0")


def exceeds_monthly_cap(employee, start_date, number_of_days, exclude_application=None):
    """True if adding this application would push the employee's start-date
    month over the MONTHLY_USAGE_CAP_DAYS usage cap."""
    already_used = cto_used_in_month(employee, start_date.year, start_date.month, exclude_application)
    return (already_used + number_of_days) > MONTHLY_USAGE_CAP_DAYS


def _date_range(start_date, end_date):
    days = []
    d = start_date
    while d <= end_date:
        days.append(d)
        d += timedelta(days=1)
    return days


def forms_illegal_consecutive_run(employee, start_date, end_date, exclude_application=None):
    """
    True if this application's date range, combined with the employee's
    other non-rejected/cancelled CTO usage immediately adjacent to it,
    would create a run of MAX_CONSECUTIVE_DAYS + 1 (i.e. 3+) consecutive
    calendar days. See module docstring for why adjacency across separate
    applications is checked, not just the single request.
    """
    from .models import CTOUsageApplication

    qs = employee.cto_usage_applications.exclude(
        status__in=[CTOUsageApplication.REJECTED, CTOUsageApplication.CANCELLED]
    )
    if exclude_application is not None:
        qs = qs.exclude(pk=exclude_application.pk)

    booked_days = set()
    for app in qs:
        booked_days.update(_date_range(app.start_date, app.end_date))
    booked_days.update(_date_range(start_date, end_date))

    if not booked_days:
        return False
    ordered = sorted(booked_days)
    run = 1
    for i in range(1, len(ordered)):
        if ordered[i] == ordered[i - 1] + timedelta(days=1):
            run += 1
            if run > MAX_CONSECUTIVE_DAYS:
                return True
        else:
            run = 1
    return False


def violates_usage_cutoff(end_date):
    """True if end_date falls after December 15 of its own year."""
    cutoff = date(end_date.year, *USAGE_CUTOFF_MONTH_DAY)
    return end_date > cutoff


def violates_filing_deadline(work_date, filed_date):
    """True if a credit claim for work_date is being filed after that
    year's November 30 deadline."""
    deadline = date(work_date.year, *FILING_DEADLINE_MONTH_DAY)
    return filed_date > deadline
