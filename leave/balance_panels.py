"""
Leave and CTO balances in the standard balance-summary look (owner request
2026-11-14; built on the Out-on-Pass branch, goes live after the pilot,
owner decision 1 = A).

Display only. Every figure comes from the existing balance logic and
ledgers; nothing is stored and no rule changes:
  - leave.balances.compute_available_balance (the figure HR's check uses),
    split into its parts from the LeaveCreditTransaction ledger;
  - cto.balances.usable_lines / CTOCreditAllocation (CTO is in DAYS, owner
    decision 2 = A: a day is one shift).

Leave: the available balance does NOT subtract applications still in the
chain (days are taken at HR recording for regular leave, at COH approval
for COSP leave). Pending days are therefore shown beside it, with the
balance "after pending" as a secondary figure.
CTO: days in a pending application are already held against credit lines
from filing, so the available figure already excludes them.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .balance_cards import CARD_CODES, SHORT_NAMES
from .balances import _ledger_total, compute_available_balance
from .models import LeaveApplication, LeaveCreditTransaction, LeaveType
from .permissions import is_chief_of_hospital

ZERO = Decimal("0")
IN_CHAIN = [LeaveApplication.SUBMITTED, LeaveApplication.ENDORSED_BY_UNIT_HEAD,
            LeaveApplication.ENDORSED_BY_SUPERVISOR, LeaveApplication.PROCESSED_BY_HR,
            LeaveApplication.RECOMMENDED_BY_AO]


def days(value):
    """'1.5 days', '1 day', '0 days'."""
    value = Decimal(value or 0).normalize()
    text = f"{value:f}"
    return f"{text} day" if value == 1 else f"{text} days"


def _pct(part, whole):
    return max(0, min(100, round(part * 100 / whole))) if whole > 0 else 0


def _ledger_sum(employee, leave_type, kinds, start, end):
    return LeaveCreditTransaction.objects.filter(
        employee=employee, leave_type=leave_type, transaction_type__in=kinds,
        transaction_date__gte=start, transaction_date__lte=end,
    ).aggregate(t=Sum("days"))["t"] or ZERO


def _pending_days(employee, leave_type):
    """Days in applications still in the chain whose days are not yet taken."""
    return (LeaveApplication.objects.filter(employee=employee, leave_type=leave_type, status__in=IN_CHAIN)
            .exclude(LeaveApplication.counted_q()).aggregate(t=Sum("number_of_days"))["t"] or ZERO)


def leave_breakdown(employee, leave_type, today):
    """Beginning + earned + used + other = available (today), for one leave type."""
    year_start = date(today.year, 1, 1)
    prev_end = year_start - timedelta(days=1)
    available = compute_available_balance(employee, leave_type, today)
    used = -_ledger_sum(employee, leave_type, [LeaveCreditTransaction.USED], year_start, today)
    other = _ledger_sum(employee, leave_type, [LeaveCreditTransaction.ADJUSTMENT, LeaveCreditTransaction.FORFEITED],
                        year_start, today)
    earned_ledger = _ledger_sum(employee, leave_type, [LeaveCreditTransaction.EARNED], year_start, today)
    if leave_type.balance_tracking == LeaveType.TRACKING_ANNUAL_CAP and not leave_type.is_cumulative:
        beginning = ZERO
        earned = (leave_type.annual_fixed_days or ZERO) + earned_ledger
    else:
        # base(d) = what the rule itself grants up to d (monthly accrual / contract-year grants),
        # read off the same function that HR's check uses. The beginning balance is the position on
        # Jan 1 before any of this year's ledger entries (Dec's accrual completes on Jan 1).
        def base(d):
            return compute_available_balance(employee, leave_type, d) - _ledger_total(employee, leave_type, end_date=d)
        beginning = base(year_start) + _ledger_total(employee, leave_type, end_date=prev_end)
        granted = base(today) - base(year_start)
        earned = granted + earned_ledger
    return {"beginning": beginning, "earned": earned, "used": used, "other": other, "available": available}


def leave_panels(employee, today=None):
    """One card per leave type shown to this employee (same types as before)."""
    today = today or timezone.localdate()
    codes = CARD_CODES.get(employee.employment_status, CARD_CODES["REGULAR"])
    types = {lt.code: lt for lt in LeaveType.objects.filter(code__in=codes, is_active=True)}
    panels = []
    for code in codes:
        lt = types.get(code)
        if lt is None:
            continue
        name = SHORT_NAMES.get(code, lt.name)
        pending = _pending_days(employee, lt)
        if lt.balance_tracking == LeaveType.TRACKING_UNTRACKED:
            used = (LeaveApplication.objects.filter(LeaveApplication.counted_q(), employee=employee, leave_type=lt,
                                                    start_date__year=today.year)
                    .aggregate(t=Sum("number_of_days"))["t"] or ZERO)
            panels.append({"name": name, "type_id": lt.pk, "value": "No fixed limit", "state": "neutral",
                           "sub": "Granted when justified under hospital policy. Expires Dec 31.",
                           "segments": [], "rows": [("Used this year", days(used)), ("Pending", days(pending))]})
            continue
        b = leave_breakdown(employee, lt, today)
        available = b["available"]
        total = b["beginning"] + b["earned"] + (b["other"] if b["other"] > 0 else ZERO)
        after = available - pending
        if available <= 0:
            state = "out"
        elif after <= 0:
            state = "low"
        else:
            state = "ok"
        rows = []
        if lt.balance_tracking == LeaveType.TRACKING_ANNUAL_CAP and not lt.is_cumulative:
            rows.append(("Granted for " + str(today.year), days(b["earned"])))
        else:
            rows += [("Beginning balance (Jan 1)", days(b["beginning"])), ("Earned this year", days(b["earned"]))]
        rows.append(("Used this year", days(b["used"])))
        if b["other"]:
            rows.append(("Adjusted / forfeited", days(b["other"])))
        rows.append(("Pending applications", days(pending)))
        panels.append({
            "name": name, "type_id": lt.pk, "value": days(max(available, ZERO)), "state": state,
            "sub": (f"After pending: {days(max(after, ZERO))}" if pending else "No pending applications")
                   + ("  ·  Expires Dec 31" if code in ("WELLNESS",) else ""),
            "segments": [
                {"kind": "used", "pct": _pct(b["used"], total), "label": "Used"},
                {"kind": "pending", "pct": _pct(min(pending, max(available, ZERO)), total), "label": "Pending"},
                {"kind": "free", "pct": _pct(max(after, ZERO), total), "label": "Available"},
            ],
            "rows": rows,
        })
    cto = cto_panel(employee, today)
    if cto is not None:  # the same CTO figures, as a compact card beside the leave types
        cards = {c["label"]: c["value"] for c in cto["cards"]}
        panels.append({
            "name": "CTO", "type_id": None, "value": cto["primary"]["value"],
            "state": cto["primary"]["state"], "sub": cto["primary"]["sub"],
            "segments": cto["primary"]["segments"],
            "rows": [("Earned credits", cards["Total earned credits"]), ("Utilized", cards["Utilized"]),
                     ("Pending applications", cards["Pending applications"])],
        })
    return panels


def cto_panel(employee, today=None):
    """The standard balance summary for CTO, in days. None for the COH (CTO does not apply)."""
    from cto.balances import MONTHLY_USAGE_CAP_DAYS, cto_used_in_month, usable_lines
    from cto.models import CTOClaimLine, CTOCreditAllocation, CTOUsageApplication

    if is_chief_of_hospital(employee):
        return None
    today = today or timezone.localdate()
    lines = list(CTOClaimLine.objects.filter(claim__employee=employee, state=CTOClaimLine.CREDITED,
                                             effective_date__lte=today))
    credited = sum((ln.credited_days for ln in lines), ZERO)
    from cto.balances import upcoming_credit, usable_from_text

    # Item 4 (owner, 2026-11-25): approved credit counts as earned at once, with "Usable from ...";
    # the available balance below still counts only credit usable today.
    earned_later = sum((d for _day, d in upcoming_credit(employee, today)), ZERO)
    usable_from = usable_from_text(employee, today)
    allocs = CTOCreditAllocation.objects.filter(line__in=lines)
    used = allocs.filter(usage_application__status=CTOUsageApplication.APPROVED).aggregate(t=Sum("days"))["t"] or ZERO
    pending = (allocs.exclude(usage_application__status=CTOUsageApplication.APPROVED)
               .aggregate(t=Sum("days"))["t"] or ZERO)
    available = sum((ln.remaining for ln in usable_lines(employee, today)), ZERO)
    soonest = next((ln for ln in usable_lines(employee, today)), None)
    if soonest is not None:
        validity = f"Use {days(soonest.remaining)} by {soonest.use_deadline:%b %d, %Y}"
    else:
        cutoff = date(today.year, *settings.CTO_USAGE_CUTOFF_MONTH_DAY)
        validity = f"Credit is used by Dec 15 of the year earned ({cutoff:%b %d, %Y} this year)"

    month_used = cto_used_in_month(employee, today.year, today.month)
    cap = MONTHLY_USAGE_CAP_DAYS
    m_state = "ok" if month_used < cap else "full"
    steps = []
    whole = int(cap)
    for i in range(whole):
        steps.append({"state": "used" if month_used >= i + 1 else ("half" if month_used > i else "free")})
    foot = "Days in a pending application are held from the day it is filed. A rejected or returned application " \
           "gives them back."
    return {
        "primary": {
            "label": "Available CTO balance", "value": days(available),
            "sub": validity, "state": "ok" if available > 0 else "out",
            "segments": [
                {"kind": "used", "pct": _pct(used, credited), "label": "Utilized"},
                {"kind": "pending", "pct": _pct(pending, credited), "label": "Pending"},
                {"kind": "free", "pct": _pct(available, credited), "label": "Available"},
            ],
            "legend": [
                {"kind": "used", "label": "Utilized", "value": days(used)},
                {"kind": "pending", "label": "Pending applications", "value": days(pending)},
            ],
        },
        "cards": [
            {"label": "Total earned credits", "value": days(credited + earned_later), "tone": "navy",
             "note": (f"{days(earned_later)} {usable_from.replace("Usable", "usable", 1)}" if earned_later else "Credited and in effect")},
            {"label": "Utilized", "value": days(used), "tone": "neutral", "note": "Approved CTO taken"},
            {"label": "Pending applications", "value": days(pending), "tone": "amber",
             "note": "Held until decided"},
        ],
        "limit": {
            "title": f"CTO in {today:%B %Y}", "state": m_state, "steps": steps,
            "value": f"{days(month_used)} of {days(cap)}",
            "sub": "Within the monthly limit." if m_state == "ok" else "Monthly limit reached.",
            "note": "At most 5 CTO days a month, and never 3 or more days in a row.",
        },
        "footnote": foot,
    }
