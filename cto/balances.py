"""
CTO compliance math, isolated from views/forms so it can be unit-tested
on its own (CLAUDE.md §7 flags CTO as the highest compliance-risk module).
Rules: CLAUDE.md §7.1 (decided 2026-11-01, pending approved HR memo).

  - credited_hours = hours_worked * multiplier for the day type (weekday
    vs rest day/holiday), from the CTOMultiplierRate in force on the
    workday. credited_days = credited_hours / employee.shift_hours (8- and
    12-hour shifts both normalize to the same "day" unit).
  - Effective date = later of the 1st of the month after the workday and
    the COH approval date. Year earned = year of the effective date. The
    balance counts only credit lines whose effective date has arrived.
  - Filing deadline for a line = Nov 30 (settings) of the year containing
    the 1st of the month after its workday.
  - Use draws from the earliest effective date first. Days are held
    against specific lines from filing (CTOCreditAllocation).
  - The "max 5 days/month, no 3+ consecutive days" limits are USAGE caps,
    and "no 3+ consecutive" also looks at adjacent applications.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

MONTHLY_USAGE_CAP_DAYS = Decimal("5")
MAX_CONSECUTIVE_DAYS = 2  # "no 3+ consecutive days" -> at most 2 in a row
# The Nov 30 filing deadline and Dec 15 usage cutoff live in settings
# (CTO_FILING_DEADLINE_MONTH_DAY / CTO_USAGE_CUTOFF_MONTH_DAY) and are read
# at call time, so a settings change takes effect without a code edit.


def compute_credit(hours_worked, is_restday_or_holiday, multiplier_rate, shift_hours):
    """
    Returns (multiplier_applied, credited_hours, credited_days) for one
    CTOClaimLine (callers pass day_type != WEEKDAY). multiplier_rate is a CTOMultiplierRate instance (the
    one active on the work date — callers look that up so the choice of
    "which rate applies" stays visible and testable on its own).

    Both results are quantized to 2 decimal places (matching
    CTOClaimLine.credited_hours/credited_days) — a plain Decimal
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


def first_of_next_month(d):
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def filing_deadline_for(work_date):
    """Nov 30 (settings) of the year containing the 1st of the month after
    the workday: November work -> Nov 30 same year; December work -> Nov 30
    of the next year."""
    return date(first_of_next_month(work_date).year, *settings.CTO_FILING_DEADLINE_MONTH_DAY)


def violates_filing_deadline(work_date, filed_date):
    return filed_date > filing_deadline_for(work_date)


def effective_date_for(work_date, approved_on):
    return max(first_of_next_month(work_date), approved_on)


def _remaining_days(line):
    used = line.allocations.aggregate(total=Sum("days"))["total"] or Decimal("0")
    return line.credited_days - used


def usable_lines(employee, as_of_date, usage_date=None):
    """Credited lines of this employee whose effective date has arrived by
    as_of_date, with days left, oldest effective date first. With
    usage_date, only lines whose Dec 15 use deadline (of the year earned)
    is on or after usage_date. Each line gets a .remaining attribute."""
    from .models import CTOClaimLine

    lines = CTOClaimLine.objects.filter(
        claim__employee=employee, state=CTOClaimLine.CREDITED, effective_date__lte=as_of_date,
    ).order_by("effective_date", "id")
    result = []
    for line in lines:
        if usage_date is not None and usage_date > date(line.year_earned, *settings.CTO_USAGE_CUTOFF_MONTH_DAY):
            continue
        line.remaining = _remaining_days(line)
        if line.remaining > 0:
            result.append(line)
    return result


def compute_available_cto_balance(employee, as_of_date=None):
    """Days available now: credited, effective, not yet used or held."""
    as_of_date = as_of_date or timezone.localdate()
    return sum((line.remaining for line in usable_lines(employee, as_of_date)), Decimal("0"))


def plan_allocation(employee, number_of_days, as_of_date, usage_date):
    """[(line, days), ...] covering number_of_days from the oldest usable
    lines, or None if there isn't enough."""
    plan = []
    needed = number_of_days
    for line in usable_lines(employee, as_of_date, usage_date):
        if needed <= 0:
            break
        take = min(line.remaining, needed)
        plan.append((line, take))
        needed -= take
    return plan if needed <= 0 else None


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
    cutoff = date(end_date.year, *settings.CTO_USAGE_CUTOFF_MONTH_DAY)
    return end_date > cutoff
