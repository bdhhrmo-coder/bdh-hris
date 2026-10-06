"""
Workforce indicators for the dashboard (owner request, 2026-10-06).

Kept apart from stats.py (request/approval counts) so each formula can be
read and unit-tested on its own. Every figure counts ACTIVE employees
unless it says otherwise. An employee attached to several sections is
counted in each of them on the per-section tables (Section/Unit is many-
to-many, CLAUDE.md §4), so section rows can add up to more than the total.

Formulas (owner decisions, 2026-10-06):

  Absenteeism rate  = unplanned absence days / scheduled workdays x 100
                      Target: 5% or lower.
      The HRIS has no duty-schedule module yet, so:
        scheduled workdays = days with an attendance record in the period
                             (present or absent)
        unplanned absence  = records marked Absent that are NOT covered by
                             approved/recorded leave, approved CTO, or
                             approved Official Business / Official Time /
                             Travel on that date.
      When a duty schedule exists, the denominator should switch to it.

  Leave utilization = leave days used / leave days earned x 100
      Leave types with an entitlement only: VL, SL, COSP Leave, Wellness.
      used   = days of approved/recorded applications starting in the period
      earned = each employee's yearly entitlement (VL/SL 15 each for
               regular staff, COSP Leave 20, Wellness 5) pro-rated to the
               days of the period they were employed.

  Permanent = active employee whose Appointment Type is "Permanent".
"""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from attendance.models import AttendanceRecord
from cto.models import CTOUsageApplication
from employees.models import EducationHistory, Employee
from leave.models import LeaveApplication, LeaveType
from official_requests.models import OfficialRequest
from orgstructure.models import Section

ABSENTEEISM_TARGET = Decimal("5.0")
UTILIZATION_LEAVE_CODES = ["VL", "SL", "COSP_LEAVE", "WELLNESS"]
AGE_BRACKETS = [(None, 24, "Below 25"), (25, 34, "25-34"), (35, 44, "35-44"), (45, 54, "45-54"),
                (55, 59, "55-59"), (60, 64, "60-64"), (65, None, "65+")]


def _rate(part, whole):
    if not whole:
        return None
    return (Decimal(part) / Decimal(whole) * 100).quantize(Decimal("0.1"))


def _active(section=None):
    qs = Employee.objects.filter(is_active=True)
    if section is not None:
        qs = qs.filter(sections=section)
    return qs.distinct()


def _sections():
    return list(Section.objects.filter(is_active=True).order_by("name"))


# -- headcount tiles ---------------------------------------------------------

def workforce_counts(start_date, end_date, section=None):
    active = _active(section)
    cosp = active.filter(employment_status=Employee.EMPLOYMENT_STATUS_COSP)
    separated = Employee.objects.filter(separation_date__range=(start_date, end_date))
    if section is not None:
        separated = separated.filter(sections=section)
    return {
        "active": active.count(),
        "separated": separated.distinct().count(),
        "permanent": active.filter(appointment_type__iexact="permanent").count(),
        "cosp": cosp.count(),
        "cosp_pgp": cosp.filter(cosp_under=Employee.COSP_UNDER_PGP).count(),
        "cosp_lgu": cosp.filter(cosp_under=Employee.COSP_UNDER_LGU).count(),
        "cosp_unassigned": cosp.filter(cosp_under="").count(),
    }


# -- absenteeism ---------------------------------------------------------------

def _days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _excused_days(employee_ids, start_date, end_date):
    """(employee_id, date) pairs covered by approved time away from work."""
    covered = set()
    sources = [
        LeaveApplication.objects.filter(status__in=[LeaveApplication.APPROVED, LeaveApplication.RECORDED]),
        CTOUsageApplication.objects.filter(status=CTOUsageApplication.APPROVED),
        OfficialRequest.objects.filter(
            status=OfficialRequest.APPROVED,
            request_type__in=[OfficialRequest.OFFICIAL_BUSINESS, OfficialRequest.OFFICIAL_TIME,
                              OfficialRequest.TRAVEL],
        ),
    ]
    for qs in sources:
        for emp_id, s, e in qs.filter(employee_id__in=employee_ids, start_date__lte=end_date,
                                      end_date__gte=start_date).values_list("employee_id", "start_date", "end_date"):
            for d in _days(max(s, start_date), min(e, end_date)):
                covered.add((emp_id, d))
    return covered


def absenteeism(start_date, end_date, section=None):
    employee_ids = list(_active(section).values_list("pk", flat=True))
    records = AttendanceRecord.objects.filter(employee_id__in=employee_ids, date__range=(start_date, end_date))
    scheduled = records.count()
    absent = list(records.filter(is_absent=True).values_list("employee_id", "date"))
    excused = _excused_days(employee_ids, start_date, end_date) if absent else set()
    unplanned = sum(1 for pair in absent if pair not in excused)
    rate = _rate(unplanned, scheduled)
    return {"unplanned": unplanned, "scheduled": scheduled, "rate": rate,
            "over_target": rate is not None and rate > ABSENTEEISM_TARGET}


# -- leave utilization ---------------------------------------------------------

def _annual_entitlement(leave_type):
    if leave_type.monthly_accrual_days:
        return leave_type.monthly_accrual_days * 12
    return leave_type.annual_fixed_days or Decimal("0")


def leave_utilization(start_date, end_date, section=None):
    types = [lt for lt in LeaveType.objects.filter(code__in=UTILIZATION_LEAVE_CODES, is_active=True)]
    employees = list(_active(section))
    earned = Decimal("0")
    for emp in employees:
        first = max(start_date, emp.date_hired) if emp.date_hired else start_date
        last = min(end_date, emp.separation_date) if emp.separation_date else end_date
        if first > last:
            continue
        share = Decimal((last - first).days + 1) / Decimal(365)
        for lt in types:
            if lt.applicable_to in (LeaveType.APPLICABLE_BOTH, emp.employment_status):
                earned += _annual_entitlement(lt) * share
    used = sum(
        (a.number_of_days for a in LeaveApplication.objects.filter(
            employee__in=employees, leave_type__in=types, start_date__range=(start_date, end_date),
            status__in=[LeaveApplication.APPROVED, LeaveApplication.RECORDED],
        )),
        Decimal("0"),
    )
    return {"used": used.quantize(Decimal("0.01")), "earned": earned.quantize(Decimal("0.01")),
            "rate": _rate(used, earned)}


def per_section(func, start_date, end_date):
    return [{"section": s, **func(start_date, end_date, section=s)} for s in _sections()]


# -- sex, age, education -------------------------------------------------------

SEX_LABELS = [(Employee.SEX_MALE, "Male"), (Employee.SEX_FEMALE, "Female"), ("", "Not recorded")]


def sex_distribution(section=None):
    counts = Counter(_active(section).values_list("sex_at_birth", flat=True))
    return [{"label": label, "count": counts.get(code, 0)} for code, label in SEX_LABELS]


def sex_by_section():
    rows = []
    for s in _sections():
        counts = Counter(_active(s).values_list("sex_at_birth", flat=True))
        rows.append({"section": s, "counts": [counts.get(code, 0) for code, _ in SEX_LABELS],
                     "total": sum(counts.values())})
    return rows


def age_of(birth, today):
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def age_distribution(section=None, today=None):
    today = today or date.today()
    counts = Counter()
    for birth in _active(section).values_list("date_of_birth", flat=True):
        if birth is None:
            counts["Not recorded"] += 1
            continue
        age = age_of(birth, today)
        for low, high, label in AGE_BRACKETS:
            if (low is None or age >= low) and (high is None or age <= high):
                counts[label] += 1
                break
    labels = [label for _, _, label in AGE_BRACKETS] + ["Not recorded"]
    return [{"label": label, "count": counts.get(label, 0)} for label in labels]


EDUCATION_COLUMNS = [label for _, label in EducationHistory.LEVEL_CHOICES] + ["Not standardized", "No record"]


def highest_education(employee_ids):
    """{employee_id: column label} for each employee's highest level."""
    best = {}
    for emp_id, level in EducationHistory.objects.filter(employee_id__in=employee_ids).values_list(
            "employee_id", "education_level"):
        rank = EducationHistory.LEVEL_RANK.get(level, -1)
        if emp_id not in best or rank > best[emp_id][0]:
            best[emp_id] = (rank, level)
    labels = dict(EducationHistory.LEVEL_CHOICES)
    result = {}
    for emp_id in employee_ids:
        if emp_id not in best:
            result[emp_id] = "No record"
        else:
            rank, level = best[emp_id]
            result[emp_id] = labels[level] if rank >= 0 else "Not standardized"
    return result


def education_by_section():
    rows = []
    for s in _sections():
        ids = list(_active(s).values_list("pk", flat=True))
        counts = Counter(highest_education(ids).values())
        rows.append({"section": s, "counts": [counts.get(c, 0) for c in EDUCATION_COLUMNS], "total": len(ids)})
    ids = list(_active().values_list("pk", flat=True))
    counts = Counter(highest_education(ids).values())
    total = {"counts": [counts.get(c, 0) for c in EDUCATION_COLUMNS], "total": len(ids)}
    return rows, total
