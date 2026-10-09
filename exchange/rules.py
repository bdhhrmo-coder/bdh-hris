"""
Exchange of Duty compliance checks, isolated the same way leave/balances.py
and cto/balances.py are, since CLAUDE.md flags §6-10 as real signed policy
and this module encodes two of its numeric limits (§8).

Batch 5 (2026-10-09) adds the schedule-based checks (same position, duty
and off days, the 7-days-before-cut-off rule before approval) in
schedule_checks.py; the rules below are unchanged.

See models.py's module docstring for the documented proxy this module
relies on (timing keyed off the exchange date itself, since no
duty-schedule module exists to check "before next month's schedule is
approved" against) and the monthly-cap scope (per employee, per calendar
month of filing).
"""

from datetime import date

MONTHLY_REQUEST_CAP = 3
MINIMUM_NOTICE_DAYS = 1  # 24 hours — the floor common to both §8 timing buckets


def violates_minimum_notice(earliest_affected_date, filed_date, is_emergency):
    """
    True if this request doesn't meet the 24-hour minimum notice §8
    requires for either a normal or a schedule-modification exchange.
    A documented emergency (§8) may be filed same-day.
    """
    if is_emergency:
        return False
    return (earliest_affected_date - filed_date).days < MINIMUM_NOTICE_DAYS


def requests_filed_in_month(employee, year, month, exclude_request=None):
    """
    Count of this employee's exchange requests (as either party) filed in
    the given calendar month, excluding dead ones (declined/rejected/
    cancelled) — a request that never went anywhere doesn't count against
    the cap.
    """
    from .models import DutyExchangeRequest

    qs = DutyExchangeRequest.objects.filter(
        filed_at__year=year, filed_at__month=month,
    ).exclude(status__in=DutyExchangeRequest.DEAD_STATUSES).filter(
        models_q_for_employee(employee)
    )
    if exclude_request is not None:
        qs = qs.exclude(pk=exclude_request.pk)
    return qs.count()


def models_q_for_employee(employee):
    from django.db.models import Q

    return Q(employee_a=employee) | Q(employee_b=employee)


def exceeds_monthly_cap(employee, filed_date=None, exclude_request=None):
    """True if this employee has already reached MONTHLY_REQUEST_CAP live
    exchange requests in filed_date's calendar month (so one more would
    exceed it)."""
    filed_date = filed_date or date.today()
    count = requests_filed_in_month(employee, filed_date.year, filed_date.month, exclude_request)
    return count >= MONTHLY_REQUEST_CAP
