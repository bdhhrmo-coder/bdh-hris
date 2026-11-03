"""
Routing for CTO claims (CLAUDE.md §6.2 / §7.1). OT requests are routed by
main's official_requests module (owner decision D4, 2026-11-02).

Routes (snapshotted on the record at submission as RoutedRecord.route):
  Claim (OT / Medical Transport): Supervisor -> [Chief Nurse] -> HR -> AO -> COH
  Exception claim: AO -> COH

Who acts at each step: main's next-level-up rules (owner decision D1,
2026-11-02; leave/permissions.py, decisions 2026-10-26/27), the same as
every other request type:
  Supervisor   endorsers_for([employee]): the unit head (incl. an OIC), else
               the section-level Supervisor, else the AO, else the COH.
  Chief Nurse  second_endorsers_for([employee]); this step is on the route
               only when needs_unit_step([employee]) (two-level endorsement).
  HR           any HR Processor/Administrator not on the claim.
  AO           the AO; the COH when the AO is on the claim.
  COH          the COH, never on a claim they are on.
Nobody on the claim (the claimant, and the filer of an exception claim)
ever acts on it. There are no skips: a step with nobody who can act waits.

submit/approve_step/return_record are atomic: if on_approved fails (e.g.
claim crediting finds no multiplier rate), the whole step rolls back and
the record stays pending at the step it was on.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from leave.permissions import (
    ao_step_actors,
    coh_step_actors,
    endorsers_for,
    hr_step_actors,
    needs_unit_step,
    second_endorsers_for,
)
from notifications.services import notify

from .models import (
    STEP_AO,
    STEP_CHIEF_NURSE,
    STEP_COH,
    STEP_HR,
    STEP_SUPERVISOR,
    RoutedRecord,
)

STEP_LABELS = {
    STEP_SUPERVISOR: "Supervisor / Unit Head",
    STEP_CHIEF_NURSE: "Chief Nurse",
    STEP_HR: "HR",
    STEP_AO: "Administrative Officer",
    STEP_COH: "Chief of Hospital",
}


def claim_route(claim):
    if claim.is_exception():
        return [STEP_AO, STEP_COH]
    steps = [STEP_SUPERVISOR]
    if needs_unit_step([claim.employee]):
        steps.append(STEP_CHIEF_NURSE)
    return steps + [STEP_HR, STEP_AO, STEP_COH]


def _subjects(record):
    """Everyone on the record: they never act on it."""
    return list(type(record.employee).objects.filter(pk__in=record.subject_employee_ids()))


def _endorser_ids(record):
    """Employees who approved the Supervisor / Chief Nurse step since the
    record was last submitted (a resubmission starts a new cycle)."""
    actions = list(record.actions.select_related("acted_by__employee").order_by("acted_at", "pk"))
    start = 0
    for i, a in enumerate(actions):
        if a.action in ("submit", "resubmit"):
            start = i
    ids = set()
    for a in actions[start:]:
        if a.action == "approve" and a.step in (STEP_SUPERVISOR, STEP_CHIEF_NURSE):
            emp = getattr(a.acted_by, "employee", None)
            if emp is not None:
                ids.add(emp.pk)
    return ids


def step_actors(record, step):
    subjects = _subjects(record)
    if step == STEP_SUPERVISOR:
        people = endorsers_for([record.employee])
    elif step == STEP_CHIEF_NURSE:
        people = second_endorsers_for([record.employee])
    elif step == STEP_HR:
        # Whoever endorsed it (Supervisor / Chief Nurse step) in this cycle
        # does not also act at the HR step (owner decision 2026-11-03, Option B).
        endorsed = _endorser_ids(record)
        people = [e for e in hr_step_actors(subjects) if e.pk not in endorsed]
    elif step == STEP_AO:
        people = ao_step_actors(subjects)
    elif step == STEP_COH:
        people = coh_step_actors(subjects)
    else:
        people = []
    excluded = {e.pk for e in subjects}
    return {e for e in people if e.pk not in excluded}


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
    notify(actors, f"{record} is waiting for your action ({STEP_LABELS[step]}).", record.get_absolute_url())


def _advance(record, from_index, on_approved):
    steps = record.route_steps()[from_index + 1:]
    if steps:
        return _wait_at(record, steps[0], step_actors(record, steps[0]))
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
