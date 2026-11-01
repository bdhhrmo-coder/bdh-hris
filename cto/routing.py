"""
Routing for OT requests and CTO claims (CLAUDE.md §6.2 / §7.1).

Routes (snapshotted on the record at submission as RoutedRecord.route):
  OT filed as Supervisor:  [Chief Nurse if the filer is a Nursing Unit Head] -> HR -> AO -> COH
  OT filed as HR:          AO -> COH
  OT filed as AO:          COH
  Claim (OT / Medical Transport): Supervisor (Unit Head in Nursing) -> [Chief Nurse in Nursing] -> HR -> AO -> COH
  Exception claim (HR Administrator): AO -> COH

Approver is the subject: nobody on the record (any OT line's employee,
the OT filer, or the claimant) may act on it; another holder of the
step's role takes it. When nobody can act on a step:
  - Supervisor / Chief Nurse: the step is skipped (no holder, or every
    holder is on the record — e.g. the Chief Nurse's own claim).
  - HR / AO: skipped only if the role has holders and all of them are on
    the record. With no holder at all, the record waits at that step and
    the HR Administrators are told.
  - COH: never skipped — nothing is approved without a COH action.
Every skip is logged as a "skip" action (audit trail).

submit/approve_step/return_record are atomic: if on_approved fails (e.g.
claim crediting finds no multiplier rate), the whole step rolls back and
the record stays pending at the step it was on.

Chief Nurse = an active Supervisor assignment scoped to the Nursing
Service Section with no unit; a Unit Head = an active Supervisor
assignment scoped to a Nursing unit. OIC coverage is an is_oic=True
Supervisor assignment, so it counts exactly as it does in supervisors_of.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment
from leave.permissions import employees_with_role, hr_employees, supervisors_of
from notifications.services import notify

from .models import (
    STEP_AO,
    STEP_CHIEF_NURSE,
    STEP_COH,
    STEP_HR,
    STEP_SUPERVISOR,
    OvertimeRequest,
    RoutedRecord,
)

STEP_LABELS = {
    STEP_SUPERVISOR: "Supervisor / Unit Head",
    STEP_CHIEF_NURSE: "Chief Nurse",
    STEP_HR: "HR",
    STEP_AO: "Administrative Officer",
    STEP_COH: "Chief of Hospital",
}
SKIPPABLE_WHEN_EMPTY = {STEP_SUPERVISOR, STEP_CHIEF_NURSE}
NEVER_SKIPPED = {STEP_COH}


def _supervisor_assignments():
    # Same filter as leave.permissions.employees_with_role: active
    # SUPERVISOR assignments, OIC included.
    return RoleAssignment.objects.filter(role=RoleAssignment.SUPERVISOR, is_active=True).select_related("employee")


def is_nursing(employee):
    name = settings.NURSING_SECTION_NAME
    return employee.units.filter(section__name=name).exists() or employee.sections.filter(name=name).exists()


def is_nursing_unit_head(employee):
    return _supervisor_assignments().filter(
        employee=employee, unit__section__name=settings.NURSING_SECTION_NAME
    ).exists()


def chief_nurses():
    return {
        ra.employee
        for ra in _supervisor_assignments().filter(section__name=settings.NURSING_SECTION_NAME, unit__isnull=True)
    }


def unit_heads_of(employee):
    unit_ids = list(employee.units.filter(section__name=settings.NURSING_SECTION_NAME).values_list("id", flat=True))
    return {ra.employee for ra in _supervisor_assignments().filter(unit_id__in=unit_ids)}


def ot_route(filer, filed_as):
    if filed_as == OvertimeRequest.CAPACITY_AO:
        return [STEP_COH]
    if filed_as == OvertimeRequest.CAPACITY_HR:
        return [STEP_AO, STEP_COH]
    steps = [STEP_CHIEF_NURSE] if is_nursing_unit_head(filer) else []
    return steps + [STEP_HR, STEP_AO, STEP_COH]


def claim_route(claim):
    if claim.is_exception():
        return [STEP_AO, STEP_COH]
    steps = [STEP_SUPERVISOR]
    if is_nursing(claim.employee):
        steps.append(STEP_CHIEF_NURSE)
    return steps + [STEP_HR, STEP_AO, STEP_COH]


def _role_holders(record, step):
    """Everyone holding the step's role for this record, before excluding
    the people on the record."""
    if step == STEP_SUPERVISOR:
        employee = record.employee
        return unit_heads_of(employee) if is_nursing(employee) else set(supervisors_of(employee))
    if step == STEP_CHIEF_NURSE:
        return chief_nurses()
    if step == STEP_HR:
        return set(hr_employees())
    if step == STEP_AO:
        return set(employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER))
    if step == STEP_COH:
        return set(employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL))
    return set()


def step_actors(record, step):
    subjects = record.subject_employee_ids()
    return {e for e in _role_holders(record, step) if e.pk not in subjects}


def people_on_route(record, route):
    """Role holders on any step of the route who are also on the record —
    used to warn an OT filer (non-blocking)."""
    subjects = record.subject_employee_ids()
    found = set()
    for step in route:
        found |= {e for e in _role_holders(record, step) if e.pk in subjects}
    return found


def can_act(employee, record):
    return (
        employee is not None
        and record.status == RoutedRecord.PENDING
        and employee in step_actors(record, record.current_step)
    )


def _log(record, action, step="", notes="", user=None):
    record.actions.create(action=action, step=step, notes=notes, acted_by=user)


def _wait_at(record, step, actors):
    record.current_step = step
    record.save(update_fields=["current_step", "status", "route", "updated_at"])
    url = record.get_absolute_url()
    if actors:
        notify(actors, f"{record} is waiting for your action ({STEP_LABELS[step]}).", url)
    else:
        notify(
            employees_with_role(RoleAssignment.HR_ADMINISTRATOR),
            f"{record} is waiting at the {STEP_LABELS[step]} step, but nobody can act on it. "
            f"Assign a {STEP_LABELS[step]}.",
            url,
        )


def _advance(record, from_index, on_approved):
    for step in record.route_steps()[from_index + 1:]:
        holders = _role_holders(record, step)
        actors = step_actors(record, step)
        if actors:
            return _wait_at(record, step, actors)
        if step in NEVER_SKIPPED or (step not in SKIPPABLE_WHEN_EMPTY and not holders):
            return _wait_at(record, step, actors)
        reason = "every holder is on this record" if holders else "no holder is assigned"
        _log(record, "skip", step, f"{STEP_LABELS[step]} step skipped: {reason}.")
    record.status = RoutedRecord.APPROVED
    record.current_step = ""
    record.approved_on = timezone.localdate()
    record.save(update_fields=["current_step", "status", "approved_on", "route", "updated_at"])
    on_approved(record)
    notify(record.owner_employee(), f"{record} was approved.", record.get_absolute_url())


@transaction.atomic
def submit(record, route, user, on_approved=lambda r: None):
    if record.status not in RoutedRecord.EDITABLE_STATUSES:
        raise ValidationError("Only a draft or returned record can be submitted.")
    resubmit = record.actions.filter(action="return").exists()
    record.route = ",".join(route)
    record.status = RoutedRecord.PENDING
    _log(record, "resubmit" if resubmit else "submit", user=user)
    _advance(record, -1, on_approved)


@transaction.atomic
def approve_step(record, employee, user, notes="", on_approved=lambda r: None):
    if not can_act(employee, record):
        raise ValidationError("You are not the approver for this step.")
    index = record.route_steps().index(record.current_step)
    _log(record, "approve", record.current_step, notes, user)
    _advance(record, index, on_approved)


@transaction.atomic
def return_record(record, employee, user, remark):
    if not can_act(employee, record):
        raise ValidationError("You are not the approver for this step.")
    remark = remark.strip()
    if not remark:
        raise ValidationError("A remark is required to return this.")
    _log(record, "return", record.current_step, remark, user)
    record.status = RoutedRecord.RETURNED
    record.current_step = ""
    record.save(update_fields=["status", "current_step", "updated_at"])
    notify(record.owner_employee(), f"{record} was returned: {remark}", record.get_absolute_url())
