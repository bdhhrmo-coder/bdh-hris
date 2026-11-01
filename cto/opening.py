"""
CTO opening balances — CLAUDE.md §7.1 "Option A" (decided 2026-11-01).

Credit earned before the HRIS existed is entered as separate opening lines,
one per piece of credit (days, date earned, required reason that can cite
the paper record) — never collapsed into one balance. Each line is a
CTOClaimLine on the employee's opening-balance CTOClaim (is_opening=True),
so balance, oldest-first use, carry-over and forfeiture treat it like any
other credit:
  - year earned = year of the date earned; counts in the balance from entry
  - date earned must be in the year of CTO_OPENING_BALANCE_DATE (2026);
    older credit goes through an exception claim
  - HR Administrator only, never for themselves; if no other HR
    Administrator exists, the AO enters them. No approval chain; every add
    and void is logged on the opening record (audit log).
  - CTO_OPENING_BALANCE_DATE only locks entry. Up to and including that
    date a wrong line is voided with a typed reason and re-entered (nothing
    is edited in place); after it, corrections go through an exception claim.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import RoleAssignment
from employees.models import Employee
from leave.permissions import employees_with_role

from .models import WEEKDAY, CTOClaim, CTOClaimLine, RoutedRecord
from .permissions import is_cto_eligible


def opening_entry_actors(employee):
    """Who may enter/void opening lines for this employee."""
    hr_admins = [e for e in employees_with_role(RoleAssignment.HR_ADMINISTRATOR) if e.pk != employee.pk]
    if hr_admins:
        return set(hr_admins)
    return {e for e in employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER) if e.pk != employee.pk}


def can_enter_opening(actor):
    """True if actor could enter opening lines for at least someone."""
    return actor is not None and (
        actor.has_role(RoleAssignment.HR_ADMINISTRATOR) or actor.has_role(RoleAssignment.ADMINISTRATIVE_OFFICER)
    )


def entry_is_open(today):
    return today <= settings.CTO_OPENING_BALANCE_DATE


def opening_record(employee, user):
    claim, _ = CTOClaim.objects.get_or_create(
        employee=employee, is_opening=True,
        defaults=dict(status=RoutedRecord.APPROVED, filed_by=user, duty_type=CTOClaim.DUTY_REGULAR),
    )
    return claim


def _check(actor, employee, today, reason):
    if actor not in opening_entry_actors(employee):
        raise ValidationError(
            "Only an HR Administrator (not for themselves) may do this; the AO does it only when no other "
            "HR Administrator exists."
        )
    if not entry_is_open(today):
        raise ValidationError(
            f"Opening balances were locked on {settings.CTO_OPENING_BALANCE_DATE:%B %d, %Y}. "
            "Use an exception claim (AO, then COH) for corrections."
        )
    if not reason.strip():
        raise ValidationError("A reason is required.")


@transaction.atomic
def add_opening_line(actor, employee, days, date_earned, reason, user, today):
    _check(actor, employee, today, reason)
    if not is_cto_eligible(employee):
        raise ValidationError("CTO is not applicable to the Chief of Hospital.")
    if days is None or days <= 0:
        raise ValidationError("Days must be greater than zero.")
    year = settings.CTO_OPENING_BALANCE_DATE.year
    if date_earned.year != year:
        raise ValidationError(f"Date earned must be in {year}. Older credit goes through an exception claim.")
    if date_earned > today:
        raise ValidationError("Date earned cannot be in the future.")
    record = opening_record(employee, user)
    line = CTOClaimLine.objects.create(
        claim=record, work_date=date_earned, hours_worked=Decimal("0"), day_type=WEEKDAY,
        shift_hours_used=employee.shift_hours, multiplier_applied=Decimal("0"), credited_hours=Decimal("0"),
        credited_days=days, state=CTOClaimLine.CREDITED, effective_date=today, year_earned=date_earned.year,
        reason=reason.strip(),
    )
    record.actions.create(
        action="opening_line_added", acted_by=user,
        notes=f"{days} day(s) earned {date_earned}: {reason.strip()}",
    )
    return line


@transaction.atomic
def void_opening_line(actor, line, reason, user, today):
    # Lock the employee row first — the same lock cto_apply takes before
    # holding days — so a usage application can't hold days against this
    # line between the check below and the void. Then re-read the line.
    Employee.objects.select_for_update().get(pk=line.claim.employee_id)
    locked = CTOClaimLine.objects.select_for_update().select_related("claim__employee").get(pk=line.pk)
    _check(actor, locked.claim.employee, today, reason)
    if not locked.claim.is_opening or locked.state != CTOClaimLine.CREDITED:
        raise ValidationError("Only an active opening line can be voided.")
    if locked.allocations.exists():
        raise ValidationError(
            "CTO has already been used or held against this line. Return or reject that CTO application first."
        )
    locked.state = CTOClaimLine.VOIDED
    locked.save(update_fields=["state"])
    locked.claim.actions.create(
        action="opening_line_voided", acted_by=user,
        notes=f"Voided {locked.credited_days} day(s) earned {locked.work_date}: {reason.strip()}",
    )
    return locked
