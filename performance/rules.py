"""Settings, tracks, eligibility, windows and periods (PMS step 2; CLAUDE.md
§13C; rebuilt 2026-11-26)."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment as R

from .models import EvaluationPeriod as P, PeriodEvent, PMSetting, RatingWindow


# -- roles -----------------------------------------------------------------------------

def is_hr(acting):
    return acting is not None and (acting.has_role(R.HR_PROCESSOR) or acting.has_role(R.HR_ADMINISTRATOR))


def is_hr_admin(acting):
    return acting is not None and acting.has_role(R.HR_ADMINISTRATOR)


def is_ao(acting, viewing=False):
    return acting is not None and acting.has_role(R.ADMINISTRATIVE_OFFICER, viewing=viewing)


def is_coh(acting, viewing=False):
    return acting is not None and acting.has_role(R.CHIEF_OF_HOSPITAL, viewing=viewing)


# -- settings ------------------------------------------------------------------------------

def setting_row(key, day=None):
    day = day or timezone.localdate()
    return PMSetting.objects.filter(key=key, effective_from__lte=day).order_by("-effective_from", "-pk").first()


def number_for(key, day=None):
    row = setting_row(key, day)
    if row is None:
        from .seed_data import SETTINGS

        return Decimal(SETTINGS[key])
    return Decimal(row.value)


def num(key, period):
    """A setting as in force on the period's end date."""
    return number_for(key, period.end_date)


@transaction.atomic
def change_setting(acting, user, key, value, effective_from, approval_ref):
    from .seed_data import SETTINGS

    if not is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator changes Performance settings.")
    if key not in SETTINGS:
        raise ValidationError("Unknown setting.")
    try:
        Decimal(str(value))
    except Exception:
        raise ValidationError("The value must be a number.")
    if effective_from < timezone.localdate():
        raise ValidationError("A change takes effect today or later, never in the past.")
    if not (approval_ref or "").strip():
        raise ValidationError("Type the PMT/COH approval reference for this change.")
    return PMSetting.objects.create(key=key, value=str(value).strip(), effective_from=effective_from,
                                    approval_ref=approval_ref.strip(), entered_by=user)


# -- who is evaluated, on which track ---------------------------------------------------

def _own_lines(emp, day):
    return emp.role_assignments.filter(R.in_force_q(day), is_oic=False)


def track_for(emp, day):
    """SUPERVISOR (own Supervisor or AO line; OIC lines never count), RANK, or
    None for the COH (not evaluated)."""
    lines = _own_lines(emp, day)
    if lines.filter(role=R.CHIEF_OF_HOSPITAL).exists():
        return None
    if lines.filter(role__in=[R.SUPERVISOR, R.ADMINISTRATIVE_OFFICER]).exists():
        return P.SUPERVISOR
    return P.RANK


def is_cosp(emp):
    return emp.employment_status == "COSP"


def in_scope(emp, day):
    if not emp.is_active or emp.archived_at is not None:
        return False
    if track_for(emp, day) is None:
        return False
    if is_cosp(emp):
        return True
    return number_for("regular_track_enabled", day) >= 1


def contracts_of(emp):
    from outonpass.models import CospContractPeriod

    return CospContractPeriod.objects.filter(employee=emp, voided_at__isnull=True).order_by("start_date")


def contract_on(emp, day):
    return contracts_of(emp).filter(start_date__lte=day, end_date__gte=day).first()


def is_first_contract(contract):
    """A newly hired COSP: no earlier contract on record and hired on or after its start."""
    earlier = contracts_of(contract.employee).filter(start_date__lt=contract.start_date).exists()
    hired = contract.employee.date_hired
    return not earlier and (hired is None or hired >= contract.start_date)


def semester_bounds(semester):
    year, half = int(semester[:4]), semester[-1]
    return (date(year, 1, 1), date(year, 6, 30)) if half == "1" else (date(year, 7, 1), date(year, 12, 31))


def midpoint(start, end):
    return start + (end - start) / 2


def semestral_eligible(emp, window):
    mid = midpoint(window.start_date, window.end_date)
    if not in_scope(emp, mid):
        return False
    if emp.date_hired and emp.date_hired > mid:
        return False  # less than the contact share of the semester
    if is_cosp(emp):
        contract = contract_on(emp, mid)
        if contract is None or is_first_contract(contract):
            return False  # first-contract COSP follow month one / quarterly
    return True


def log(period, action, user, note=""):
    PeriodEvent.objects.create(period=period, action=action, note=(note or "")[:500], user=user)


# -- windows --------------------------------------------------------------------------------

def create_window(acting, user, semester, opens_on, closes_on):
    if not is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator creates rating windows.")
    if len(semester) != 7 or semester[4:6] != "-S" or semester[-1] not in "12":
        raise ValidationError("Write the semester as YYYY-S1 or YYYY-S2.")
    if RatingWindow.objects.filter(semester=semester).exists():
        raise ValidationError("There is already a window for that semester.")
    if closes_on <= opens_on:
        raise ValidationError("The window must close after it opens.")
    start, end = semester_bounds(semester)
    half = "First" if semester[-1] == "1" else "Second"
    return RatingWindow.objects.create(name=f"{half} semester {semester[:4]}", semester=semester, start_date=start,
                                       end_date=end, opens_on=opens_on, closes_on=closes_on, created_by=user)


@transaction.atomic
def open_window(acting, user, window):
    """Opens the window and creates a semestral period (with its raters) for
    every eligible employee. Returns a list of warnings."""
    from employees.holidays import year_has_holidays
    from employees.models import Employee

    from . import raters

    if not is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator opens rating windows.")
    if window.status != RatingWindow.DRAFT:
        raise ValidationError("Only a draft window can be opened.")
    if RatingWindow.objects.filter(status=RatingWindow.OPEN).exists():
        raise ValidationError("Another window is still open. Close it first.")
    warnings = []
    if not year_has_holidays(window.closes_on.year):
        warnings.append(f"The holiday list for {window.closes_on.year} is empty, so due dates count every Monday "
                        "to Friday. Add the holidays on HR Settings -> Holidays.")
    window.status, window.opened_at = RatingWindow.OPEN, timezone.now()
    window.save(update_fields=["status", "opened_at"])
    mid = midpoint(window.start_date, window.end_date)
    for emp in Employee.objects.filter(is_active=True, archived_at__isnull=True):
        if not semestral_eligible(emp, window):
            continue
        period, created = P.objects.get_or_create(
            employee=emp, window=window, period_type=P.SEMESTRAL,
            defaults=dict(track=track_for(emp, mid), start_date=window.start_date, end_date=window.end_date,
                          contract=contract_on(emp, mid) if is_cosp(emp) else None))
        if created:
            log(period, "created", user, f"{window.name} opened")
            raters.build_assignments(period, user)
    return warnings


def close_window(acting, user, window):
    if not is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator closes rating windows.")
    if window.status != RatingWindow.OPEN:
        raise ValidationError("The window is not open.")
    window.status, window.closed_at = RatingWindow.CLOSED, timezone.now()
    window.save(update_fields=["status", "closed_at"])


# -- COSP periods ------------------------------------------------------------------------------

def add_months(day, n):
    m = day.month - 1 + n
    y, m = day.year + m // 12, m % 12 + 1
    return date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def cosp_plan(contract):
    """[(period_type, start, end)] for a contract (v1.1 periods, decision 5).
    First contract: month one, quarterly, pre-expiry (a quarter ending within
    pre_expiry_replace_days of the pre-expiry end is replaced by it).
    Later contract: pre-expiry only (semestral comes from the window)."""
    day = contract.start_date
    pre_end = contract.end_date - timedelta(days=int(number_for("pre_expiry_days", day)))
    replace = timedelta(days=int(number_for("pre_expiry_replace_days", day)))
    if pre_end <= contract.start_date:
        return []
    out = []
    start = contract.start_date
    if is_first_contract(contract):
        m1_end = add_months(start, 1) - timedelta(days=1)
        if m1_end < pre_end - replace:
            out.append((P.MONTH1, start, m1_end))
            start = m1_end + timedelta(days=1)
            while True:
                q_end = add_months(start, 3) - timedelta(days=1)
                if q_end >= pre_end - replace:
                    break
                out.append((P.QUARTER, start, q_end))
                start = q_end + timedelta(days=1)
    out.append((P.PRE_EXPIRY, start, pre_end))
    return out


@transaction.atomic
def plan_cosp_periods(acting, user, contract):
    """HR may plan a contract's periods at once; otherwise the daily job does it."""
    if not is_hr(acting):
        raise ValidationError("Only HR plans COSP periods.")
    if track_for(contract.employee, contract.start_date) is None:
        raise ValidationError("This employee is not evaluated.")
    return _plan(contract, user)


@transaction.atomic
def open_planned(acting, user, period):
    """HR may open a planned period early; otherwise the daily job opens it."""
    if not is_hr(acting):
        raise ValidationError("Only HR opens COSP periods.")
    if acting.pk == period.employee_id:
        raise ValidationError("You cannot open your own period.")
    if period.status != P.PLANNED:
        raise ValidationError("Only a planned period can be opened.")
    _open(period, user)


# -- automatic COSP periods (decision 1, owner 2026-11-26, Option B) ----------------------------

def _plan(contract, user=None):
    """Creates the contract's planned periods that have not ended yet."""
    emp = contract.employee
    track = track_for(emp, contract.start_date)
    if track is None:
        return 0
    today, made = timezone.localdate(), 0
    for kind, start, end in cosp_plan(contract):
        if end < today:
            continue  # never back-filled: a period that already ended is not created by the job
        period, created = P.objects.get_or_create(employee=emp, contract=contract, period_type=kind, start_date=start,
                                                  defaults=dict(track=track, end_date=end, status=P.PLANNED))
        if created:
            log(period, "planned", user, "planned from the contract")
            made += 1
    return made


def _open(period, user=None):
    from . import raters

    period.status = P.OPEN
    period.save(update_fields=["status"])
    log(period, "opened", user, "" if user else "opened by the daily job")
    raters.build_assignments(period, user)


def auto_cosp_periods(today=None):
    """Daily job: plan the periods of every live COSP contract, and open each
    planned period `cosp_open_days_before_end` days before it ends (raters are
    assigned and told then; forms are due `cosp_rating_days` working days after
    the end). HR may plan or open earlier by hand, or cancel with a reason."""
    from outonpass.models import CospContractPeriod

    today = today or timezone.localdate()
    planned = opened = 0
    for c in CospContractPeriod.objects.filter(voided_at__isnull=True, end_date__gte=today).select_related("employee"):
        emp = c.employee
        if is_cosp(emp) and emp.is_active and emp.archived_at is None:
            planned += _plan(c)
    for p in P.objects.filter(status=P.PLANNED).select_related("employee"):
        lead = timedelta(days=int(number_for("cosp_open_days_before_end", p.end_date)))
        if today >= p.end_date - lead and p.employee.is_active and p.employee.archived_at is None:
            _open(p)
            opened += 1
    return planned, opened


@transaction.atomic
def cancel_period(acting, user, period, reason):
    from .models import RaterAssignment as A

    if not is_hr_admin(acting) or acting.pk == period.employee_id:
        raise ValidationError("An HR Administrator cancels a period (never their own).")
    if period.status not in (P.PLANNED, P.OPEN):
        raise ValidationError("Only a planned or open period can be cancelled.")
    if not (reason or "").strip():
        raise ValidationError("Type the reason.")
    period.raters.filter(status__in=A.LIVE | {A.PROPOSED}).update(status=A.VOID)
    period.status = P.CANCELLED
    period.save(update_fields=["status"])
    log(period, "cancelled", user, reason.strip())
