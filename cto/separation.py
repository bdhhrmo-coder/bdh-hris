"""
Separating employees (CLAUDE.md §7.1, decided 2026-11-01).

COSP who will not renew or has resigned; Regular who has resigned or
retired. HR (Processor or Administrator) sets the status and last day of
service; the change is audit-logged (EmployeeEditHistory). While it is
set, the effective-date wait, the Dec 15 use deadline, the 5-days-per-
month limit and the 3-consecutive-days limit are all waived; CTO can be
used up to the last day of service and the Supervisor still approves the
dates. After the last day, the nightly lapse job closes any unused credit
and any claim still in the approval chain. Clearing the status restores
the normal rules; anything already lapsed stays lapsed.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import RoleAssignment
from leave.permissions import ao_step_actors, hr_step_actors
from employees.models import Employee, EmployeeEditHistory

ALLOWED = {
    Employee.EMPLOYMENT_STATUS_COSP: {Employee.SEPARATION_NOT_RENEWING, Employee.SEPARATION_RESIGNED},
    Employee.EMPLOYMENT_STATUS_REGULAR: {Employee.SEPARATION_RESIGNED, Employee.SEPARATION_RETIRED},
}


def is_separating(employee):
    """True while a separation status and last day are set: the waivers
    apply (use is still limited to dates up to the last day of service)."""
    return bool(employee.separation_status and employee.last_day_of_service)


def separation_actors(employee):
    """Who may set this employee's separation status: any other HR Processor
    or Administrator; the AO when there is none (owner decision D3)."""
    return set(hr_step_actors([employee])) or set(ao_step_actors([employee]))


def can_set_separation(actor):
    """True if actor could set someone's separation status (opens the page)."""
    return actor is not None and (
        actor.has_role(RoleAssignment.HR_PROCESSOR) or actor.has_role(RoleAssignment.HR_ADMINISTRATOR)
        or actor.has_role(RoleAssignment.ADMINISTRATIVE_OFFICER)
    )


@transaction.atomic
def set_separation(actor, employee, status, last_day, reason, user):
    """status "" with last_day None clears it."""
    if actor.pk == employee.pk:
        # The status waives the actor's own CTO deadlines and limits.
        raise ValidationError("You cannot set your own separation status; another HR Processor or Administrator must.")
    if actor not in separation_actors(employee):
        raise ValidationError("Only HR can set a separation status (the AO only when no other HR staff exists).")
    if not reason.strip():
        raise ValidationError("A reason is required.")
    if status:
        if status not in ALLOWED.get(employee.employment_status, set()):
            raise ValidationError(
                f"{dict(Employee.SEPARATION_STATUS_CHOICES)[status]} does not apply to a "
                f"{employee.get_employment_status_display()} employee."
            )
        if last_day is None:
            raise ValidationError("Enter the last day of service.")
    else:
        last_day = None
    changes = []
    for field, label, new in (("separation_status", "Separation status", status),
                              ("last_day_of_service", "Last day of service", last_day)):
        old = getattr(employee, field)
        if old != new:
            changes.append((field, label, old, new))
            setattr(employee, field, new)
    if not changes:
        return employee
    employee.save(update_fields=[c[0] for c in changes] + ["updated_at"])
    for field, label, old, new in changes:
        EmployeeEditHistory.objects.create(
            employee=employee, field_name=field, field_label=label,
            old_value="" if old in (None, "") else str(old), new_value="" if new in (None, "") else str(new),
            reason=reason.strip(), changed_by=user,
        )
    return employee


@transaction.atomic
def run_separation_lapse(today):
    """Nightly: for employees whose last day of service has passed, lapse
    unused credit and any claim still in the approval chain. Safe to run
    again. Returns (lines lapsed, claims lapsed)."""
    from notifications.services import notify

    from .carryover import remaining_days
    from .models import CTOClaim, CTOClaimLine, RoutedRecord

    lines_lapsed = claims_lapsed = 0
    for employee in Employee.objects.exclude(separation_status="").filter(last_day_of_service__lt=today):
        days = 0
        for line in CTOClaimLine.objects.filter(
            claim__employee=employee, state__in=[CTOClaimLine.CREDITED, CTOClaimLine.CARRY_PENDING]
        ):
            left = remaining_days(line)
            if left <= 0:
                continue
            line.state, line.closed_days, line.closed_on = CTOClaimLine.LAPSED, left, today
            line.save(update_fields=["state", "closed_days", "closed_on"])
            days += left
            lines_lapsed += 1
        for claim in CTOClaim.objects.filter(
            employee=employee, status__in=[RoutedRecord.PENDING, RoutedRecord.RETURNED]
        ):
            claim.status, claim.current_step = RoutedRecord.LAPSED, ""
            claim.save(update_fields=["status", "current_step", "updated_at"])
            claim.actions.create(action="lapse", notes=f"Lapsed: last day of service was {employee.last_day_of_service}.")
            claims_lapsed += 1
        if days:
            notify(employee, f"{days} day(s) of unused CTO lapsed after your last day of service.", "/cto/mine/")
    return lines_lapsed, claims_lapsed
