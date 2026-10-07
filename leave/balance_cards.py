"""
"My Balances" cards on the Apply for Leave page (Batch 2, Item 5).

Display only. Every number comes from the existing balance functions
(leave.balances.compute_available_balance, cto.balances.
compute_available_cto_balance) and the existing ledgers - nothing new is
stored or entered.

Per card:
  remaining = the existing available balance (today)
  used      = days used this calendar year (ledger USED entries, or for
              Emergency Leave, which has no balance, its approved/recorded
              applications this year)
  total     = remaining + used (what the person had to work with this year)

Colour (proposed thresholds - no existing ones):
  red    = 1 day or less left
  yellow = 25% of the total or less left
  green  = everything else
  grey   = no fixed limit (Emergency Leave)
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from cto.balances import compute_available_cto_balance
from cto.models import CTOCreditTransaction

from .balances import compute_available_balance
from .models import LeaveApplication, LeaveCreditTransaction, LeaveType
from .permissions import is_chief_of_hospital

RED_AT_OR_BELOW_DAYS = Decimal("1")
YELLOW_AT_OR_BELOW_SHARE = Decimal("0.25")

CARD_CODES = {
    "REGULAR": ["VL", "SL", "WELLNESS", "EMERGENCY"],
    "COSP": ["COSP_LEAVE", "WELLNESS", "EMERGENCY"],
}
SHORT_NAMES = {"VL": "Vacation Leave", "SL": "Sick Leave", "WELLNESS": "Wellness Leave",
               "EMERGENCY": "Emergency Leave", "COSP_LEAVE": "COSP Leave"}
NON_CUMULATIVE_NOTE = "Expires Dec 31"


def status_colour(remaining, total):
    if remaining is None:
        return "neutral"
    if remaining <= RED_AT_OR_BELOW_DAYS:
        return "red"
    if total and remaining <= total * YELLOW_AT_OR_BELOW_SHARE:
        return "yellow"
    return "green"


def _card(key, name, remaining, used, note="", type_id=None):
    remaining = remaining if remaining is None else max(remaining, Decimal("0"))
    total = None if remaining is None else remaining + used
    percent = 0
    if total:
        percent = int((remaining / total * 100).quantize(Decimal("1")))
    return {"key": key, "name": name, "type_id": type_id, "remaining": remaining, "used": used, "total": total,
            "percent": percent, "colour": status_colour(remaining, total), "note": note}


def _used_leave_this_year(employee, leave_type, today):
    total = LeaveCreditTransaction.objects.filter(
        employee=employee, leave_type=leave_type, transaction_type=LeaveCreditTransaction.USED,
        transaction_date__gte=date(today.year, 1, 1), transaction_date__lte=today,
    ).aggregate(t=Sum("days"))["t"] or Decimal("0")
    return -total


def _emergency_used_this_year(employee, leave_type, today):
    return LeaveApplication.objects.filter(
        employee=employee, leave_type=leave_type, start_date__year=today.year,
        status__in=[LeaveApplication.APPROVED, LeaveApplication.RECORDED],
    ).aggregate(t=Sum("number_of_days"))["t"] or Decimal("0")


def balance_cards(employee, today=None):
    today = today or date.today()
    codes = CARD_CODES.get(employee.employment_status, CARD_CODES["REGULAR"])
    types = {lt.code: lt for lt in LeaveType.objects.filter(code__in=codes, is_active=True)}
    cards = []
    for code in codes:
        lt = types.get(code)
        if lt is None:
            continue
        note = NON_CUMULATIVE_NOTE if code in ("WELLNESS", "EMERGENCY") else ""
        if lt.balance_tracking == LeaveType.TRACKING_UNTRACKED:
            cards.append(_card(code, SHORT_NAMES.get(code, lt.name), None,
                               _emergency_used_this_year(employee, lt, today), note, lt.pk))
        else:
            cards.append(_card(code, SHORT_NAMES.get(code, lt.name), compute_available_balance(employee, lt, today),
                               _used_leave_this_year(employee, lt, today), note, lt.pk))

    if not is_chief_of_hospital(employee):  # CTO is not applicable to the Chief of Hospital (CLAUDE.md §7)
        used = -(CTOCreditTransaction.objects.filter(
            employee=employee, transaction_type=CTOCreditTransaction.USED,
            transaction_date__gte=date(today.year, 1, 1), transaction_date__lte=today,
        ).aggregate(t=Sum("days"))["t"] or Decimal("0"))
        cutoff = date(today.year, *settings.CTO_USAGE_CUTOFF_MONTH_DAY)
        cards.append(_card("CTO", "CTO", compute_available_cto_balance(employee, today), used,
                           f"Use by {cutoff:%b} {cutoff.day}"))
    return cards
