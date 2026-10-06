"""
Opening leave and CTO balance import (go-live, 2026-10-06).

Why it is needed: the system works out VL/SL as 1.25 days for every month
since Date Hired, plus whatever is in the ledger. It knows nothing about
leave taken before the HRIS existed, so without an opening balance a long-
serving employee would show far too many days.

How it works: HR enters each employee's balance from the leave card as of
a cut-over date. For every value given, the system compares it with what
it would itself compute as of that date, and records the difference as a
"Manual adjustment" ledger entry dated that day, with a note saying where
it came from. So afterwards the system shows exactly the leave-card
balance on that date, and normal accrual and usage continue from there.
Importing the same file again is harmless: the difference is then zero
and nothing is added.

Blank cell = leave that balance alone.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction

from cto.balances import compute_available_cto_balance
from cto.models import CTOCreditTransaction
from cto.permissions import is_cto_eligible
from employees.models import Employee
from leave.balances import compute_available_balance
from leave.models import LeaveCreditTransaction, LeaveType

from .common import parse_date, parse_decimal, read_sheet, text

SHEET = "Opening Balances"
COLUMNS = [
    ("employee_id", "Employee ID*"),
    ("name", "Name (for reference only)"),
    ("as_of", "As-of Date*"),
    ("VL", "Vacation Leave (VL)"),
    ("SL", "Sick Leave (SL)"),
    ("COSP_LEAVE", "COSP Leave"),
    ("WELLNESS", "Wellness Leave (remaining this year)"),
    ("CTO", "CTO (days)"),
]
LEAVE_CODES = ["VL", "SL", "COSP_LEAVE", "WELLNESS"]
LABELS = {"VL": "VL", "SL": "SL", "COSP_LEAVE": "COSP Leave", "WELLNESS": "Wellness", "CTO": "CTO"}
MAX_DAYS = Decimal("999")


@dataclass
class BalanceLine:
    code: str
    current: Decimal
    target: Decimal

    @property
    def adjustment(self):
        return self.target - self.current


@dataclass
class BalancePlan:
    row: int
    employee_id: str
    name: str = ""
    as_of: date = None
    lines: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def action(self):
        return "update" if any(line.adjustment for line in self.lines) else "no change"

    @property
    def changes(self):
        return [f"{LABELS[l.code]}: {l.current} → {l.target}" for l in self.lines if l.adjustment]


def build_plan(uploaded_file, today=None):
    today = today or date.today()
    rows = read_sheet(uploaded_file, SHEET, COLUMNS)
    leave_types = {lt.code: lt for lt in LeaveType.objects.filter(code__in=LEAVE_CODES)}
    plans, seen = [], {}
    for row_no, raw in rows:
        eid = text(raw["employee_id"])
        p = BalancePlan(row=row_no, employee_id=eid, name=text(raw["name"]))
        plans.append(p)
        if not eid:
            p.errors.append("Employee ID is required.")
            continue
        if eid.lower() in seen:
            p.errors.append(f"Employee ID {eid} is also on row {seen[eid.lower()]}.")
        seen[eid.lower()] = row_no
        employee = Employee.objects.filter(employee_id__iexact=eid).first()
        if employee is None:
            p.errors.append(f"No employee with ID {eid}. Import the employee list first.")
            continue
        p.name = employee.full_name

        as_of, err = parse_date(raw["as_of"])
        if err or as_of is None:
            p.errors.append(f"As-of Date: {err or 'required'}.")
            continue
        if as_of > today:
            p.errors.append("As-of Date cannot be in the future.")
            continue
        p.as_of = as_of

        for code in LEAVE_CODES + ["CTO"]:
            value, err = parse_decimal(raw[code])
            if err:
                p.errors.append(f"{LABELS[code]}: {err}.")
                continue
            if value is None:
                continue
            if value < 0 or value > MAX_DAYS:
                p.errors.append(f"{LABELS[code]}: must be between 0 and {MAX_DAYS}.")
                continue
            if code == "CTO":
                if not is_cto_eligible(employee):
                    p.errors.append("CTO does not apply to the Chief of Hospital (CLAUDE.md §7).")
                    continue
                current = compute_available_cto_balance(employee, as_of_date=as_of)
            else:
                lt = leave_types.get(code)
                if lt is None:
                    p.errors.append(f"Leave type {code} is not set up in the system.")
                    continue
                if lt.applicable_to not in (LeaveType.APPLICABLE_BOTH, employee.employment_status):
                    p.errors.append(f"{LABELS[code]} does not apply to a {employee.get_employment_status_display()} employee.")
                    continue
                if not lt.is_cumulative and lt.annual_fixed_days is not None and value > lt.annual_fixed_days:
                    p.errors.append(f"{LABELS[code]}: cannot be more than {lt.annual_fixed_days} days a year.")
                    continue
                current = compute_available_balance(employee, lt, as_of_date=as_of)
            p.lines.append(BalanceLine(code=code, current=Decimal(current).quantize(Decimal("0.01")), target=value.quantize(Decimal("0.01"))))
    return plans


@transaction.atomic
def apply_plan(plans, actor_user, source_name):
    """Save a plan that has NO errors. Returns the number of ledger entries added."""
    assert not any(p.errors for p in plans), "apply_plan called with errors"
    added = 0
    leave_types = {lt.code: lt for lt in LeaveType.objects.filter(code__in=LEAVE_CODES)}
    for p in plans:
        employee = Employee.objects.get(employee_id__iexact=p.employee_id)
        for line in p.lines:
            if not line.adjustment:
                continue
            note = f"Opening balance {line.target} as of {p.as_of:%Y-%m-%d} (import: {source_name})"[:255]
            if line.code == "CTO":
                CTOCreditTransaction.objects.create(
                    employee=employee, transaction_type=CTOCreditTransaction.ADJUSTMENT, days=line.adjustment,
                    transaction_date=p.as_of, notes=note, created_by=actor_user,
                )
            else:
                LeaveCreditTransaction.objects.create(
                    employee=employee, leave_type=leave_types[line.code],
                    transaction_type=LeaveCreditTransaction.ADJUSTMENT, days=line.adjustment,
                    transaction_date=p.as_of, notes=note, created_by=actor_user,
                )
            added += 1
    return added
