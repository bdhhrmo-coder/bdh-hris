"""
Year-end CTO carry-over (CLAUDE.md §7.1, decided 2026-11-01).

Each credited line has a use_deadline: Dec 15 of its year earned, or Dec 15
of the next year when it took effect after Dec 15 (those carry over with no
reason). A line is AT RISK for year Y when its use_deadline is Dec 15 of Y
and it still has unused days. At-risk credit survives Dec 15 only with a
carry-over reason, submitted by end of Dec 16 and approved by the COH:
  - approved: the line's use_deadline moves to Dec 15 of Y+1 (and needs a
    new reason next year if still unused)
  - still pending on Dec 17: the line waits (CARRY_PENDING, not usable)
  - rejected or never submitted: forfeited on Dec 17; a rejection after
    Dec 15 forfeits pending credit at once. A rejection is final.
Separating employees are skipped: their Dec 15 deadline is waived and
their credit lapses after the last day of service instead.

Who submits: a Supervisor (Unit Head in Nursing) covering the employee;
if none, the Chief Nurse for Nursing staff, otherwise an HR Administrator.
An HR Administrator's own credit is submitted by the AO, and the AO's by an
HR Administrator. Never the employee. The COH decides every reason.
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import RoleAssignment
from leave.permissions import employees_with_role, supervisors_of
from notifications.services import notify

from . import routing
from .models import CTOCarryOverReason, CTOClaimLine
from .separation import is_separating

CARRY_URL = "/cto/carry-over/"


def use_deadline(year):
    return date(year, *settings.CTO_USAGE_CUTOFF_MONTH_DAY)


def reason_deadline(year):
    return date(year, *settings.CTO_CARRYOVER_REASON_DEADLINE_MONTH_DAY)


def initial_use_deadline(effective_date, year_earned):
    """Dec 15 of the year earned; a line that took effect after that date
    gets the next year's Dec 15 (it carries over without a reason)."""
    deadline = use_deadline(year_earned)
    return use_deadline(year_earned + 1) if effective_date > deadline else deadline


def fill_missing_use_deadlines(line_model):
    """One-time backfill (migration 0008): give every credited line without
    a use_deadline the one it would have got when credited. Takes the model
    class so the migration can pass its historical model. Returns the count."""
    filled = 0
    for line in line_model.objects.filter(
        state__in=["CREDITED", "CARRY_PENDING"], use_deadline__isnull=True, effective_date__isnull=False
    ):
        line.use_deadline = initial_use_deadline(line.effective_date, line.year_earned or line.effective_date.year)
        line.save(update_fields=["use_deadline"])
        filled += 1
    return filled


def remaining_days(line):
    used = line.allocations.aggregate(total=Sum("days"))["total"] or Decimal("0")
    return line.credited_days - used


def at_risk_lines(employee, year, states=(CTOClaimLine.CREDITED,)):
    lines = CTOClaimLine.objects.filter(
        claim__employee=employee, state__in=states, use_deadline=use_deadline(year)
    ).order_by("effective_date", "id")
    result = []
    for line in lines:
        line.remaining = remaining_days(line)
        if line.remaining > 0:
            result.append(line)
    return result


def at_risk_employees(year):
    """Employees with unused credit whose use deadline is Dec 15 of year,
    excluding separating employees (their deadline is waived)."""
    from employees.models import Employee

    ids = (
        CTOClaimLine.objects.filter(state=CTOClaimLine.CREDITED, use_deadline=use_deadline(year))
        .values_list("claim__employee_id", flat=True).distinct()
    )
    return [
        e for e in Employee.objects.filter(pk__in=ids)
        if at_risk_lines(e, year) and not is_separating(e)
    ]


def reason_submitters(employee):
    def others(people):
        return {p for p in people if p.pk != employee.pk}

    if employee.has_role(RoleAssignment.HR_ADMINISTRATOR):
        return others(employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER))
    if employee.has_role(RoleAssignment.ADMINISTRATIVE_OFFICER):
        return others(employees_with_role(RoleAssignment.HR_ADMINISTRATOR))
    nursing = routing.is_nursing(employee)
    covering = others(routing.unit_heads_of(employee) if nursing else supervisors_of(employee))
    if not covering and nursing:
        covering = others(routing.chief_nurses())
    if not covering:
        covering = others(employees_with_role(RoleAssignment.HR_ADMINISTRATOR))
    return covering


@transaction.atomic
def submit_reason(submitter, employee, year, reason, today):
    if submitter not in reason_submitters(employee):
        raise ValidationError("You cannot submit a carry-over reason for this employee.")
    if today > reason_deadline(year) or today.year != year:
        raise ValidationError(f"Carry-over reasons for {year} had to be submitted by {reason_deadline(year):%B %d, %Y}.")
    if not reason.strip():
        raise ValidationError("A written reason is required.")
    if CTOCarryOverReason.objects.filter(employee=employee, year=year).exists():
        raise ValidationError(f"A carry-over reason for {employee} for {year} was already submitted.")
    if not at_risk_lines(employee, year):
        raise ValidationError(f"{employee} has no unused {year} CTO that would need a carry-over reason.")
    record = CTOCarryOverReason.objects.create(
        employee=employee, year=year, reason=reason.strip(), submitted_by=submitter
    )
    notify(employee, f"{submitter} submitted a reason to carry your unused {year} CTO past Dec 15. "
                     "The Chief of Hospital will decide.", "/cto/mine/")
    notify(employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL),
           f"A CTO carry-over reason for {employee} ({year}) awaits your decision.", CARRY_URL)
    return record


def _carry(line, year):
    line.state = CTOClaimLine.CREDITED
    line.use_deadline = use_deadline(year + 1)
    line.save(update_fields=["state", "use_deadline"])


def _forfeit(line, today):
    line.closed_days = remaining_days(line)
    line.state = CTOClaimLine.FORFEITED
    line.closed_on = today
    line.save(update_fields=["state", "closed_days", "closed_on"])


@transaction.atomic
def decide_reason(coh, record, approve, note, today):
    if not coh.has_role(RoleAssignment.CHIEF_OF_HOSPITAL):
        raise ValidationError("Only the Chief of Hospital decides carry-over reasons.")
    if record.status != CTOCarryOverReason.PENDING:
        raise ValidationError("This reason was already decided.")
    record.status = CTOCarryOverReason.APPROVED if approve else CTOCarryOverReason.REJECTED
    record.decided_by = coh
    record.decided_at = timezone.now()
    record.decision_note = note.strip()
    record.save()
    forfeited = Decimal("0")
    if today > use_deadline(record.year):
        # After Dec 15 the decision takes effect at once; before it, the
        # credit stays usable and the Dec 17 job applies the decision.
        for line in at_risk_lines(record.employee, record.year,
                                  states=(CTOClaimLine.CREDITED, CTOClaimLine.CARRY_PENDING)):
            if approve:
                _carry(line, record.year)
            else:
                forfeited += line.remaining
                _forfeit(line, today)
    verdict = "approved" if approve else "rejected"
    detail = (f" {forfeited} day(s) were forfeited." if forfeited else
              "" if approve else " Unused credit is forfeited after Dec 15.")
    notify([record.employee, record.submitted_by],
           f"The Chief of Hospital {verdict} carrying {record.employee}'s {record.year} CTO past Dec 15.{detail}",
           "/cto/mine/")
    return record


@transaction.atomic
def run_year_end(today):
    """The daily year-end job. Looks at every credited line whose use
    deadline (Dec 15 of year Y) has passed AND whose reason deadline
    (Dec 16 of Y) has passed — so it does nothing new until Dec 17 each
    year, and also closes out stale credit from earlier years (e.g. a
    separating employee whose status was cleared after Dec 17). Per line:
    approved reason for (employee, Y) -> carry over; pending -> hold;
    rejected or none -> forfeit. Separating employees are skipped. Safe to
    run every day. Returns counts per outcome."""
    counts = {"carried": 0, "pending": 0, "forfeited": 0}
    forfeited = {}
    lines = CTOClaimLine.objects.filter(
        state=CTOClaimLine.CREDITED, use_deadline__lt=today
    ).select_related("claim__employee")
    for line in lines:
        year = line.use_deadline.year
        employee = line.claim.employee
        if today <= reason_deadline(year) or is_separating(employee):
            continue
        left = remaining_days(line)
        if left <= 0:
            continue
        reason = CTOCarryOverReason.objects.filter(employee=employee, year=year).first()
        if reason and reason.status == CTOCarryOverReason.APPROVED:
            _carry(line, year)
            counts["carried"] += 1
        elif reason and reason.status == CTOCarryOverReason.PENDING:
            line.state = CTOClaimLine.CARRY_PENDING
            line.save(update_fields=["state"])
            counts["pending"] += 1
        else:
            forfeited[(employee, year)] = forfeited.get((employee, year), Decimal("0")) + left
            _forfeit(line, today)
            counts["forfeited"] += 1
    for (employee, year), days in forfeited.items():
        notify(employee, f"{days} day(s) of your {year} CTO were forfeited: unused after Dec 15 with no "
                         "approved carry-over reason.", "/cto/mine/")
    return counts


def send_reminders(today):
    """Carry-over reminders, sent only on the dates in
    CTO_CARRYOVER_REMINDER_DATES. Returns None on other days, else a
    summary dict."""
    from leave.permissions import hr_employees

    if (today.month, today.day) not in [tuple(d) for d in settings.CTO_CARRYOVER_REMINDER_DATES]:
        return None
    year = today.year
    submitted = set(CTOCarryOverReason.objects.filter(year=year).values_list("employee_id", flat=True))
    waiting = [e for e in at_risk_employees(year) if e.pk not in submitted]
    deadline = f"{reason_deadline(year):%b %d}"

    by_submitter = {}
    for employee in waiting:
        for submitter in reason_submitters(employee):
            by_submitter.setdefault(submitter, []).append(employee)
    for submitter, staff in by_submitter.items():
        names = ", ".join(e.full_name for e in staff)
        notify(submitter, f"CTO carry-over: {len(staff)} of your staff have unused {year} CTO that is forfeited "
                          f"after Dec 15 unless you submit a reason by {deadline}: {names}", CARRY_URL)
    if waiting:
        names = ", ".join(e.full_name for e in waiting)
        notify(hr_employees(), f"CTO carry-over: {len(waiting)} employee(s) with at-risk {year} CTO and no reason "
                               f"yet (due {deadline}): {names}", CARRY_URL)
    pending = CTOCarryOverReason.objects.filter(status=CTOCarryOverReason.PENDING).count()
    if pending:
        notify(employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL),
               f"CTO carry-over: {pending} reason(s) await your decision.", CARRY_URL)
    return {"at_risk_without_reason": len(waiting), "submitters": len(by_submitter), "pending_reasons": pending}
