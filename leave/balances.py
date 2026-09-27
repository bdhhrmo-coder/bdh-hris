"""
Leave balance math, isolated from views/forms so it's easy to unit test in
isolation — CLAUDE.md flags leave-accrual math as compliance-risk, same as
the CTO multiplier math.

Reference date used for both accrual styles: Employee.date_hired. CLAUDE.md
does not say which date should anchor accrual, so this is a documented
assumption, not a guess buried in code — flagged to the project owner
alongside the rest of Phase 4.

Rules implemented (see LeaveType docstring for the by-type breakdown):
  - ACCRUED (VL, SL): earns monthly_accrual_days for every FULL month of
    service completed by as_of_date, cumulative (no reset).
  - ANNUAL_CAP, cumulative (COSP Leave): the full annual_fixed_days becomes
    available at the start of each contract year (year of date_hired, then
    every anniversary), and unused days carry into the next year.
  - ANNUAL_CAP, non-cumulative (Wellness): annual_fixed_days is available
    fresh each CALENDAR year regardless of tenure, and does not carry over.
  - UNTRACKED: no numeric cap is enforced; returns None.
"""

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from .models import LeaveCreditTransaction, LeaveType


def months_of_service(reference_date, as_of_date):
    """Full calendar months completed between two dates (0 if not yet a full month)."""
    if reference_date is None or as_of_date < reference_date:
        return 0
    months = (as_of_date.year - reference_date.year) * 12 + (as_of_date.month - reference_date.month)
    if as_of_date.day < reference_date.day:
        months -= 1
    return max(months, 0)


def contract_years_started(reference_date, as_of_date):
    """
    How many annual grants have started by as_of_date, counting the first
    one on date_hired itself. 0 if date_hired is missing or in the future.
    """
    if reference_date is None or as_of_date < reference_date:
        return 0
    return months_of_service(reference_date, as_of_date) // 12 + 1


def _ledger_total(employee, leave_type, start_date=None, end_date=None):
    qs = LeaveCreditTransaction.objects.filter(employee=employee, leave_type=leave_type)
    if start_date is not None:
        qs = qs.filter(transaction_date__gte=start_date)
    if end_date is not None:
        qs = qs.filter(transaction_date__lte=end_date)
    return qs.aggregate(total=Sum("days"))["total"] or Decimal("0")


def compute_available_balance(employee, leave_type, as_of_date=None):
    """
    Returns the available balance as a Decimal, or None if this leave type
    has no system-enforced cap (balance_tracking == UNTRACKED).
    """
    as_of_date = as_of_date or date.today()

    if leave_type.balance_tracking == LeaveType.TRACKING_ACCRUED:
        months = months_of_service(employee.date_hired, as_of_date)
        earned = (leave_type.monthly_accrual_days or Decimal("0")) * months
        return earned + _ledger_total(employee, leave_type, end_date=as_of_date)

    if leave_type.balance_tracking == LeaveType.TRACKING_ANNUAL_CAP:
        annual = leave_type.annual_fixed_days or Decimal("0")
        if leave_type.is_cumulative:
            grants = contract_years_started(employee.date_hired, as_of_date)
            earned = annual * grants
            return earned + _ledger_total(employee, leave_type, end_date=as_of_date)
        else:
            year_start = date(as_of_date.year, 1, 1)
            used_this_year = _ledger_total(employee, leave_type, start_date=year_start, end_date=as_of_date)
            return annual + used_this_year

    return None  # UNTRACKED
