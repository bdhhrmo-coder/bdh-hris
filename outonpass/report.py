"""
Monthly Out-on-Pass report to the Chief of Hospital (CLAUDE.md 13B; policy
monitoring section): hours per unit, third passes, exceptions and emergency
passes, time not covered (HR's list for minor attendance corrections),
passes out and not back, passes never used, low balances, and staff with
third passes in 3 or more months of the year (for supervisor review).
Display only - every figure comes from the pass records and rules.py.
"""

from collections import OrderedDict
from datetime import date

from django.conf import settings

from employees.models import Employee

from . import gate, rules
from .models import OutOnPass as P

COUNTED = P.LIVE_STATUSES + (P.COMPLETED,)


def low_balance_minutes():
    return settings.OUT_ON_PASS_RULES.get("LOW_BALANCE_MINUTES", 60)


def area_of(e):
    units = [u.name for u in e.units.all()]
    if units:
        return ", ".join(units)
    return ", ".join(s.name for s in e.sections.all()) or "(no section)"


def monthly(year, month):
    passes = list(P.objects.filter(pass_date__year=year, pass_date__month=month, status__in=COUNTED)
                  .select_related("employee").prefetch_related("employee__units", "employee__sections")
                  .order_by("pass_date", "time_out"))

    by_area = OrderedDict()
    for p in passes:
        row = by_area.setdefault(area_of(p.employee), {"area": area_of(p.employee), "passes": 0, "completed": 0,
                                                       "minutes": 0, "excess": 0, "over_balance": 0,
                                                       "third": 0, "exception": 0, "emergency": 0})
        row["passes"] += 1
        if p.status == P.COMPLETED:
            row["completed"] += 1
            row["minutes"] += p.minutes_charged or 0
            row["excess"] += p.excess_minutes or 0
            row["over_balance"] += p.over_balance_minutes or 0
        key = {P.THIRD: "third", P.EXCEPTION: "exception", P.EMERGENCY: "emergency"}.get(p.kind)
        if key:
            row[key] += 1
    areas = sorted(by_area.values(), key=lambda r: r["area"])
    totals = {k: sum(r[k] for r in areas) for k in ("passes", "completed", "minutes", "excess", "over_balance",
                                                    "third", "exception", "emergency")}

    special = [p for p in passes if p.is_special]
    not_covered = [p for p in passes if p.status == P.COMPLETED and ((p.excess_minutes or 0) or
                                                                     (p.over_balance_minutes or 0))]

    # Staff with third passes in 3 or more months of the year (policy: review by the supervisor).
    third_months = {}
    for emp_id, m in (P.objects.filter(pass_date__year=year, pass_date__month__lte=month, kind=P.THIRD,
                                       status__in=COUNTED).values_list("employee_id", "pass_date__month")):
        third_months.setdefault(emp_id, set()).add(m)
    repeat_ids = [k for k, v in third_months.items() if len(v) >= 3]
    repeaters = [(e, sorted(third_months[e.pk])) for e in Employee.objects.filter(pk__in=repeat_ids)]

    on = date(year, month, 1)
    low = []
    for e in (Employee.objects.filter(is_active=True, archived_at__isnull=True)
              .prefetch_related("units", "sections")):
        allowance = rules.allowance_minutes(e, year)
        if allowance <= 0:
            continue
        bal = rules.balance_minutes(e, year)
        if bal <= low_balance_minutes():
            low.append({"employee": e, "area": area_of(e), "allowance": allowance, "balance": bal})
    low.sort(key=lambda r: (r["balance"], r["employee"].surname))

    return {
        "year": year, "month": month, "label": on.strftime("%B %Y"),
        "areas": areas, "totals": totals, "special": special, "not_covered": not_covered,
        "not_back": gate.not_back(), "unused": gate.unused(), "repeaters": repeaters, "low": low,
        "low_limit": low_balance_minutes(),
    }


def export_tables(r):
    """(heading, header, rows) for the Excel and PDF exports."""
    hm = rules.hm
    return [
        ("Hours per unit", ["Unit / Section", "Passes", "Completed", "Hours charged", "Excess (min)",
                            "Not covered by balance (min)", "Third passes", "Exceptions", "Emergency passes"],
         [[a["area"], a["passes"], a["completed"], hm(a["minutes"]), a["excess"], a["over_balance"], a["third"],
           a["exception"], a["emergency"]] for a in r["areas"]]
         + [["TOTAL", r["totals"]["passes"], r["totals"]["completed"], hm(r["totals"]["minutes"]),
             r["totals"]["excess"], r["totals"]["over_balance"], r["totals"]["third"], r["totals"]["exception"],
             r["totals"]["emergency"]]]),
        ("Third passes, exceptions and emergency passes", ["Pass no.", "Employee", "Unit / Section", "Type",
                                                           "Date", "Reason", "Status", "Charged"],
         [[p.pass_number, p.employee.full_name, area_of(p.employee), p.get_kind_display(), f"{p.pass_date:%b %d}",
           p.get_reason_type_display(), p.get_status_display(),
           hm(p.minutes_charged) if p.minutes_charged is not None else ""] for p in r["special"]]),
        ("Time not covered (HR: minor attendance correction)", ["Pass no.", "Employee", "Employment", "Date",
                                                                "Charged", "Excess (min)",
                                                                "Not covered by balance (min)", "Note"],
         [[p.pass_number, p.employee.full_name, p.employee.get_employment_status_display(), f"{p.pass_date:%b %d}",
           hm(p.minutes_charged or 0), p.excess_minutes or 0, p.over_balance_minutes or 0,
           "Exception approved by the COH" if p.kind == P.EXCEPTION else ""] for p in r["not_covered"]]),
        ("Out, not back (HR to close)", ["Pass no.", "Employee", "Date", "Expected in", "Tapped out"],
         [[p.pass_number, p.employee.full_name, f"{p.pass_date:%b %d}", f"{p.time_in:%I:%M %p}",
           f"{p.tapped_out_at:%I:%M %p}"] for p in r["not_back"]]),
        ("Approved but never tapped out", ["Pass no.", "Employee", "Date", "Planned"],
         [[p.pass_number, p.employee.full_name, f"{p.pass_date:%b %d}",
           f"{p.time_out:%I:%M %p} - {p.time_in:%I:%M %p}"] for p in r["unused"]]),
        (f"Low or exhausted balances ({rules.hm(r['low_limit'])} or less)", ["Employee", "Unit / Section",
                                                                             "Allowance", "Balance"],
         [[x["employee"].full_name, x["area"], hm(x["allowance"]), hm(x["balance"])] for x in r["low"]]),
        ("Third passes in 3 or more months (for supervisor review)", ["Employee", "Months"],
         [[e.full_name, ", ".join(date(r["year"], m, 1).strftime("%b") for m in months)]
          for e, months in r["repeaters"]]),
    ]
