"""
Group (batch) filing of Travel, Official Business and Authorized OT /
rest-day / holiday work - Batch 2, Item 7 (owner decisions 2026-10-07).

WHO MAY FILE
  Supervisor        - Travel / OB: only employees they supervise (incl. as OIC).
                      OT: any employee (CLAUDE.md §7.1).
  HR Processor      - any employee; every type (OT added by D5, 2026-11-02).
  HR Administrator  - any employee; every type.
  AO                - any employee; OT only (D5).
  Never the COH or the filer themselves. Someone holding several of these
  roles files under the highest one that allows the chosen type: AO, then
  HR Administrator, then HR Processor, then Supervisor (8b, 2026-11-02).
  A unit head with a section head above files OT as "Unit Head" (D6).

ROUTE (the filer's own step counts as done; the whole batch moves together)
  OT / rest day / holiday   by Supervisor: HR -> AO -> COH
                            by Unit Head:  section head -> HR -> AO -> COH (D6)
                            by HR:         AO -> COH
                            by AO:         COH (D6)
  Travel                    by Supervisor: AO -> COH
                            by HR:         AO -> COH
  Official Business         by Supervisor: HR -> AO -> COH
                            by HR:         Supervisor -> AO -> COH
                            (the Supervisor still endorses - owner decision)
  A regular employee filing their own request keeps the normal route
  (single request form, routing.py) - nothing here changes that.

The filer can never act on their own batch at a later step, whatever other
roles (or OIC) they hold. Approving an OT batch creates NO CTO credit: the
employee files a CTO claim from the approved line (CLAUDE.md §7.1). OT lines
dated in the past need a typed reason; none may fall after the employee's
last day of service. Supervisors covering an employee put on someone else's
OT batch get an information-only notice (D5).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction

from accounts.models import RoleAssignment
from cto.models import CTOUsageApplication
from employees.models import Employee
from exchange.models import DutyExchangeRequest
from leave.models import LeaveApplication
from leave.permissions import (
    _supervisor_assignments,
    ao_step_actors,
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    coh_step_actors,
    endorsers_for,
    second_endorsers_for,
    hr_step_actors,
    supervisors_of,
)
from notifications.services import notify

from .models import OfficialRequest as R
from .models import OfficialRequestAction, OfficialRequestBatch as B, OfficialRequestBatchAction

MAX_DATE_LINES = 31
MAX_ENTRIES = 300

# kind code -> (label, request_type, work_kind)
KINDS = {
    "TRAVEL": ("Local Official Errand", R.TRAVEL, ""),
    "OFFICIAL_BUSINESS": ("Official Business (Travel Order)", R.OFFICIAL_BUSINESS, ""),
    "OT": ("Authorized overtime", R.OT_RESTDAY_HOLIDAY, B.WORK_OT),
    "REST_DAY": ("Rest-day work", R.OT_RESTDAY_HOLIDAY, B.WORK_REST_DAY),
    "HOLIDAY": ("Holiday work", R.OT_RESTDAY_HOLIDAY, B.WORK_HOLIDAY),
}
WORK_KINDS = {"OT", "REST_DAY", "HOLIDAY"}

KINDS_BY_ROLE = {
    B.FILER_AO: set(WORK_KINDS),
    B.FILER_HR_ADMINISTRATOR: set(KINDS),
    B.FILER_HR_PROCESSOR: set(KINDS),
    B.FILER_SUPERVISOR: set(KINDS),
}
ROLE_PRIORITY = [B.FILER_AO, B.FILER_HR_ADMINISTRATOR, B.FILER_HR_PROCESSOR, B.FILER_SUPERVISOR]

HR_ROLES = {B.FILER_HR_PROCESSOR, B.FILER_HR_ADMINISTRATOR}
ANY_EMPLOYEE_ROLES = HR_ROLES | {B.FILER_AO}


def _route_key(request_type, filer_role):
    if filer_role in HR_ROLES:
        return request_type, "HR"
    if filer_role in (B.FILER_AO, B.FILER_UNIT_HEAD):
        return request_type, filer_role
    return request_type, "SUPERVISOR"


ROUTES = {
    (R.OT_RESTDAY_HOLIDAY, "SUPERVISOR"): [R.PROCESSED_BY_HR, R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.OT_RESTDAY_HOLIDAY, "HR"): [R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.OT_RESTDAY_HOLIDAY, B.FILER_UNIT_HEAD): [R.ENDORSED_BY_SUPERVISOR, R.PROCESSED_BY_HR, R.RECOMMENDED_BY_AO,
                                                R.APPROVED],
    (R.OT_RESTDAY_HOLIDAY, B.FILER_AO): [R.APPROVED],
    # Local Official Errand (owner decision 2026-11-03): approved by the AO.
    (R.TRAVEL, "SUPERVISOR"): [R.APPROVED],
    (R.TRAVEL, "HR"): [R.APPROVED],
    # Official Business (Travel Order): the COH recommends approval, the
    # Governor approves at the Capitol and HR records it (2026-11-03).
    (R.OFFICIAL_BUSINESS, "SUPERVISOR"): [R.PROCESSED_BY_HR, R.RECOMMENDED_BY_AO, R.FORWARDED_TO_CAPITOL, R.APPROVED],
    (R.OFFICIAL_BUSINESS, "HR"): [R.ENDORSED_BY_SUPERVISOR, R.RECOMMENDED_BY_AO, R.FORWARDED_TO_CAPITOL, R.APPROVED],
}
# Steps of the normal single-request route that the batch route leaves out.
SKIPPED = {
    (R.OT_RESTDAY_HOLIDAY, "SUPERVISOR"): "Supervisor endorsement",
    (R.OT_RESTDAY_HOLIDAY, "HR"): "Supervisor endorsement and HR processing",
    (R.OT_RESTDAY_HOLIDAY, B.FILER_UNIT_HEAD): "Unit Head endorsement",
    (R.OT_RESTDAY_HOLIDAY, B.FILER_AO): "Supervisor endorsement, HR processing and AO recommendation",
    (R.TRAVEL, "SUPERVISOR"): "Supervisor endorsement",
    (R.TRAVEL, "HR"): "Supervisor endorsement",
    (R.OFFICIAL_BUSINESS, "SUPERVISOR"): "Supervisor endorsement",
    (R.OFFICIAL_BUSINESS, "HR"): "HR processing",
}
STEP_ACTIONS = {
    R.ENDORSED_BY_SUPERVISOR: ("endorse", "Endorse", "Supervisor"),
    R.PROCESSED_BY_HR: ("process", "Process", "HR"),
    R.RECOMMENDED_BY_AO: ("recommend", "Recommend", "Administrative Officer"),
    R.FORWARDED_TO_CAPITOL: ("forward", "Recommend approval (forward to the Capitol)",
                             "Chief of Hospital (recommending approval)"),
    R.APPROVED: ("approve", "Approve", "Chief of Hospital"),
}
TYPE_STEP_ACTIONS = {
    (R.TRAVEL, R.APPROVED): ("approve", "Approve", "Administrative Officer"),
    (R.OFFICIAL_BUSINESS, R.APPROVED): ("record_approval", "Record the Governor's approval",
                                        "Governor (recorded by HR)"),
}


def step_action(request_type, status):
    return TYPE_STEP_ACTIONS.get((request_type, status)) or STEP_ACTIONS[status]


# -- who may file -------------------------------------------------------------

def filer_roles(acting_employee):
    if acting_employee is None:
        return []
    held = {
        B.FILER_AO: acting_employee.has_role(RoleAssignment.ADMINISTRATIVE_OFFICER),
        B.FILER_HR_ADMINISTRATOR: acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR),
        B.FILER_HR_PROCESSOR: acting_employee.has_role(RoleAssignment.HR_PROCESSOR),
        B.FILER_SUPERVISOR: acting_employee.has_role(RoleAssignment.SUPERVISOR),
    }
    return [role for role in ROLE_PRIORITY if held[role]]


def allowed_kinds(acting_employee):
    kinds = set()
    for role in filer_roles(acting_employee):
        kinds |= KINDS_BY_ROLE[role]
    return [k for k in KINDS if k in kinds]


def is_unit_head_with_section_head(employee):
    """A Supervisor scoped to units only, with a section-level Supervisor
    above them (Nursing: a unit head under the Chief Nurse)."""
    own = _supervisor_assignments(employee)
    return bool(own) and all(ra.unit_id for ra in own) and bool(second_endorsers_for([employee]))


def filing_role_for(acting_employee, kind):
    for role in filer_roles(acting_employee):
        if kind in KINDS_BY_ROLE[role]:
            if role == B.FILER_SUPERVISOR and kind in WORK_KINDS and is_unit_head_with_section_head(acting_employee):
                return B.FILER_UNIT_HEAD
            return role
    return None


def supervised_employees(acting_employee, viewing=False):
    """Active employees this Supervisor (or OIC) covers. viewing=True for
    view-only use (homepage figures), see Employee.active_role_assignments."""
    qs = Employee.objects.filter(is_active=True)
    section_ids, unit_ids = set(), set()
    for ra in acting_employee.active_role_assignments(viewing=viewing).filter(role=RoleAssignment.SUPERVISOR):
        if ra.section_id is None and ra.unit_id is None:
            return qs  # hospital-wide assignment
        if ra.section_id:
            section_ids.add(ra.section_id)
        if ra.unit_id:
            unit_ids.add(ra.unit_id)
    from django.db.models import Q

    return qs.filter(Q(sections__in=section_ids) | Q(units__in=unit_ids)).distinct()


def employees_for(acting_employee, filer_role, kind):
    if filer_role in ANY_EMPLOYEE_ROLES or (filer_role and kind in WORK_KINDS):
        return Employee.objects.filter(is_active=True)  # OT: anyone (§7.1); the form leaves out the COH and self
    if filer_role == B.FILER_SUPERVISOR:
        return supervised_employees(acting_employee)
    return Employee.objects.none()


def pickable_employees(acting_employee):
    """Everyone this person could include under any of their filing roles
    (the form narrows it again once the type is chosen). Every filing role
    may file OT for anyone, so that is all active employees."""
    if filer_roles(acting_employee):
        return Employee.objects.filter(is_active=True)
    return Employee.objects.none()


def common_supervisors(employees, exclude_employee=None):
    """Who may endorse for EVERY employee given (the OB-by-HR Supervisor
    step): each employee's next-level-up endorser (leave.permissions.
    endorsers_for), never one of the employees or the filer (owner
    decision 2026-10-26)."""
    employees = list(employees)
    excluded = {e.pk for e in employees}
    if exclude_employee is not None:
        excluded.add(exclude_employee.pk)
    common = None
    for emp in employees:
        # Group requests keep ONE Supervisor endorsement: the unit head or the
        # section head may give it (so a section head can endorse a group that
        # spans several units). Two-level endorsement applies to individual
        # requests only (owner decision 2026-10-27).
        ids = ({s.pk for s in endorsers_for([emp])} | {s.pk for s in second_endorsers_for([emp])}) - excluded
        common = ids if common is None else common & ids
        if not common:
            return []
    return list(Employee.objects.filter(pk__in=common or []))


def unit_head_step_actors(batch):
    """OT filed by a unit head: the section head above the filer (D6). Nobody
    on the batch acts on it; with no section head left, the next level up:
    the AO, or the COH when the AO is on it (8i, D1)."""
    filer = getattr(batch.filed_by, "employee", None)
    if filer is None:
        return []
    people = batch_people(batch)
    excluded = {p.pk for p in people}
    heads = [s for s in second_endorsers_for([filer]) if s.pk not in excluded]
    return heads or ao_step_actors(people)


def batch_people(batch):
    """The filer and every employee on the batch: none of them acts on it."""
    people = [line.employee for line in batch.active_lines()]
    filer = getattr(batch.filed_by, "employee", None)
    if filer is not None:
        people.append(filer)
    return people


# -- route ----------------------------------------------------------------------

def route(batch):
    return ROUTES[_route_key(batch.request_type, batch.filer_role)]


def skipped_steps(batch):
    return SKIPPED[_route_key(batch.request_type, batch.filer_role)]


def next_status(batch):
    steps = route(batch)
    if batch.status == R.SUBMITTED:
        return steps[0]
    if batch.status in steps:
        i = steps.index(batch.status)
        return steps[i + 1] if i + 1 < len(steps) else None
    return None


def route_labels(batch):
    return ["Filed by " + batch.get_filer_role_display()] + [step_action(batch.request_type, s)[2] for s in route(batch)]


def may_act(acting_employee, user, batch):
    if acting_employee is None or batch.filed_by_id == user.pk:
        return False  # the filer never acts on their own batch again
    target = next_status(batch)
    people = batch_people(batch)
    if acting_employee.pk in {p.pk for p in people}:
        return False  # never on a batch that includes them (owner decision 2026-10-26)
    if target == R.ENDORSED_BY_SUPERVISOR:
        if batch.filer_role == B.FILER_UNIT_HEAD:
            return acting_employee.pk in {s.pk for s in unit_head_step_actors(batch)}
        lines = list(batch.active_lines())
        return bool(lines) and acting_employee.pk in {
            s.pk for s in common_supervisors([line.employee for line in lines])}
    from .routing import approver_role

    role = approver_role(batch.request_type, target)
    if role == "HR":
        return can_act_hr_step(acting_employee, people, batch)
    if role == "AO":
        return can_act_ao_step(acting_employee, people)
    if role == "COH":
        return can_act_coh_step(acting_employee, people)
    return False


def available_actions(acting_employee, user, batch):
    if not may_act(acting_employee, user, batch):
        return []
    code, label, _ = step_action(batch.request_type, next_status(batch))
    if batch.status == R.FORWARDED_TO_CAPITOL:
        # At the Capitol: HR records the Governor's decision; no return.
        return [(code, label), ("reject", "Record disapproval")]
    return [(code, label), ("return", "Return"), ("reject", "Reject")]


def batches_awaiting(acting_employee, user):
    open_batches = B.objects.exclude(status__in=R.TERMINAL_STATUSES | {R.RETURNED}).order_by("filed_at", "pk")
    return [b for b in open_batches if may_act(acting_employee, user, b)]


# -- entries and conflicts --------------------------------------------------------

def hours_between(time_from, time_to):
    """Hours from start to end; an end at or before the start is the next day."""
    if not time_from or not time_to:
        return None
    start = datetime.combine(datetime.min, time_from)
    end = datetime.combine(datetime.min, time_to)
    if end <= start:
        end += timedelta(days=1)
    return (Decimal((end - start).seconds) / Decimal(3600)).quantize(Decimal("0.01"))


def _covers(qs, emp_field="employee_id"):
    for emp_id, s, e in qs.values_list(emp_field, "start_date", "end_date"):
        d = s
        while d <= e:
            yield emp_id, d
            d += timedelta(days=1)


def find_conflicts(entries, exclude_batch=None):
    """{(employee_id, date): [reason, ...]} for entries that clash with
    approved leave, approved CTO use, an approved Exchange of Duty, or
    another open/approved request for the same date."""
    if not entries:
        return {}
    emp_ids = {e["employee"].pk for e in entries}
    dates = {e["date"] for e in entries}
    lo, hi = min(dates), max(dates)
    wanted = {(e["employee"].pk, e["date"]) for e in entries}
    found = {}

    def flag(key, reason):
        if key in wanted and reason not in found.setdefault(key, []):
            found[key].append(reason)

    leave = LeaveApplication.objects.filter(employee_id__in=emp_ids, start_date__lte=hi, end_date__gte=lo,
                                            status__in=[LeaveApplication.APPROVED, LeaveApplication.RECORDED])
    for key in _covers(leave):
        flag(key, "On approved leave")
    cto = CTOUsageApplication.objects.filter(employee_id__in=emp_ids, start_date__lte=hi, end_date__gte=lo,
                                             status=CTOUsageApplication.APPROVED)
    for key in _covers(cto):
        flag(key, "On approved CTO")
    exchanges = DutyExchangeRequest.objects.filter(status=DutyExchangeRequest.APPROVED)
    for ex in exchanges.filter(employee_a_id__in=emp_ids) | exchanges.filter(employee_b_id__in=emp_ids):
        for emp_id in (ex.employee_a_id, ex.employee_b_id):
            for d in (ex.date_a, ex.date_b):
                flag((emp_id, d), "Approved Exchange of Duty on this date")
    others = R.objects.filter(employee_id__in=emp_ids, start_date__lte=hi, end_date__gte=lo).exclude(
        status__in=[R.REJECTED, R.CANCELLED])
    if exclude_batch is not None:
        others = others.exclude(batch=exclude_batch)
    for req in others:
        d = req.start_date
        while d <= req.end_date:
            status = "approved" if req.status == R.APPROVED else "pending"
            flag((req.employee_id, d), f"Already has a {status} {req.get_request_type_display()} request")
            d += timedelta(days=1)
    return found


# -- saving and actions ---------------------------------------------------------------

def _log(batch, action, user, notes, lines=None):
    OfficialRequestBatchAction.objects.create(batch=batch, action=action, resulting_status=batch.status,
                                              notes=notes[:500], acted_by=user)
    for line in (lines if lines is not None else batch.active_lines()):
        OfficialRequestAction.objects.create(request=line, action=action, resulting_status=line.status,
                                             notes=f"Batch #{batch.pk}: {notes}"[:255], acted_by=user)


def _line_values(batch, entry):
    restday = batch.work_kind in (B.WORK_REST_DAY, B.WORK_HOLIDAY)
    is_ot = batch.request_type == R.OT_RESTDAY_HOLIDAY
    return dict(
        request_type=batch.request_type, employee=entry["employee"], start_date=entry["date"], end_date=entry["date"],
        time_from=entry.get("time_from"), time_to=entry.get("time_to"), destination=batch.destination,
        expenses_authorized=batch.expenses_authorized, transportation_allowed=batch.transportation_allowed,
        purpose=batch.purpose, is_restday_or_holiday=restday,
        hours_requested=hours_between(entry.get("time_from"), entry.get("time_to")) if is_ot else None,
        line_note=entry.get("note", "")[:255], status=batch.status,
    )


def filing_note(batch, count):
    role = batch.get_filer_role_display()
    return (f"Filed on behalf of {count} employee-date line(s) by {role}. "
            f"Skipped step: {skipped_steps(batch)} (filed on behalf by {role}).")


@transaction.atomic
def create_batch(user, filer_role, kind, purpose, destination, entries, expenses="", transport="",
                 past_date_reason=""):
    _, request_type, work_kind = KINDS[kind]
    is_ob = request_type == R.OFFICIAL_BUSINESS
    batch = B.objects.create(request_type=request_type, work_kind=work_kind, purpose=purpose,
                             destination=destination if request_type in (R.TRAVEL, R.OFFICIAL_BUSINESS) else "",
                             expenses_authorized=expenses if is_ob else "",
                             transportation_allowed=transport if request_type in (R.OFFICIAL_BUSINESS, R.TRAVEL) else "",
                             past_date_reason=past_date_reason if request_type == R.OT_RESTDAY_HOLIDAY else "",
                             filed_by=user, filer_role=filer_role)
    lines = [R.objects.create(batch=batch, **_line_values(batch, e)) for e in entries]
    _log(batch, "submit", user, filing_note(batch, len(lines)), lines)
    notify_filed(batch, new_employee_ids={e["employee"].pk for e in entries})
    return batch


_FILING_ACTIONS = ("submit", "resubmit", "edit")


def pending_untouched(batch):
    """Still waiting at its first step, with no approver action since it was
    filed or resubmitted (owner request 2026-11-03: the filer may edit it)."""
    if batch.status in R.TERMINAL_STATUSES or batch.status == R.RETURNED:
        return False
    last = batch.actions.order_by("-acted_at", "-pk").first()
    return last is not None and last.action in _FILING_ACTIONS


def filer_may_edit(batch, user):
    return batch.filed_by_id == user.pk and (batch.status == R.RETURNED or pending_untouched(batch))


@transaction.atomic
def resubmit_batch(batch, user, purpose, destination, entries, note="", expenses="", transport="",
                   past_date_reason=""):
    """The filer's edit: after a return, or while still pending with nobody
    having acted yet (2026-11-03). Same batch, lines updated/added, removed
    lines cancelled (kept for the audit trail). A returned batch starts again
    from the first step; a pending one simply stays at its first step."""
    locked = B.objects.select_for_update().get(pk=batch.pk)
    if not filer_may_edit(locked, user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("An approver acted on this group request while you were editing it.")
    was_pending = batch.status != R.RETURNED
    batch.purpose = purpose
    batch.destination = destination if batch.request_type in (R.TRAVEL, R.OFFICIAL_BUSINESS) else ""
    if batch.request_type == R.OFFICIAL_BUSINESS:
        batch.expenses_authorized, batch.transportation_allowed = expenses, transport
    elif batch.request_type == R.TRAVEL:
        batch.transportation_allowed = transport  # the F59 vehicle (2026-11-03)
    batch.past_date_reason = past_date_reason if batch.request_type == R.OT_RESTDAY_HOLIDAY else ""
    batch.status = R.SUBMITTED
    batch.save()
    existing = {(line.employee_id, line.start_date): line for line in batch.active_lines()}
    keep, before = set(), set(existing)
    for e in entries:
        key = (e["employee"].pk, e["date"])
        values = _line_values(batch, e)
        if key in existing:
            line = existing[key]
            for field, value in values.items():
                setattr(line, field, value)
            line.save()
        else:
            R.objects.create(batch=batch, **values)
        keep.add(key)
    for key in before - keep:
        line = existing[key]
        line.status = R.CANCELLED
        line.save(update_fields=["status", "updated_at"])
        OfficialRequestAction.objects.create(request=line, action="cancel", resulting_status=R.CANCELLED,
                                             notes=f"Batch #{batch.pk}: removed by the filer"
                                                   f"{' before any approver acted' if was_pending else ' after a return'}.",
                                             acted_by=user)
    if was_pending:
        _log(batch, "edit", user, note or "Edited by the filer before any approver acted.")
    else:
        _log(batch, "resubmit", user, note or "Edited and resubmitted after a return.")
    notify_filed(batch, new_employee_ids={k[0] for k in keep - before},
                 resubmitted=not was_pending, edited=was_pending)
    return batch


_RESULT = {"return": R.RETURNED, "reject": R.REJECTED}


@transaction.atomic
def apply_action(batch, user, action, notes):
    """Move the WHOLE batch (and every active line) one step, or return /
    reject it. Never creates CTO credit."""
    new_status = _RESULT.get(action) or next_status(batch)
    batch.status = new_status
    batch.save(update_fields=["status", "updated_at"])
    batch.lines.exclude(status=R.CANCELLED).update(status=new_status)
    _log(batch, action, user, notes)
    notify_step(batch, action, notes)


# -- notifications ---------------------------------------------------------------------

def _url(batch):
    return f"/official-requests/batch/{batch.pk}/"


def _next_actors(batch):
    target = next_status(batch)
    involved = batch_people(batch)
    from .routing import approver_role

    role = approver_role(batch.request_type, target) if target else None
    if role == "SUPERVISOR" and batch.filer_role == B.FILER_UNIT_HEAD:
        people = unit_head_step_actors(batch)
    elif role == "SUPERVISOR":
        # Either the unit head or the section head may endorse a group, but
        # only the unit head(s) are notified when there is one for everybody
        # (owner 2026-11-03, dry-run item 4: not several Supervisors).
        employees = [line.employee for line in batch.active_lines()]
        people = common_supervisors(employees)
        unit_ids = set.intersection(*[{s.pk for s in endorsers_for([e])} for e in employees]) if employees else set()
        preferred = [p for p in people if p.pk in unit_ids]
        people = preferred or people
    elif role == "HR":
        people = hr_step_actors(involved, batch)
    elif role == "AO":
        people = ao_step_actors(involved)
    elif role == "COH":
        people = coh_step_actors(involved)
    else:
        people = []
    involved_ids = {p.pk for p in involved}
    return [p for p in people if p.pk not in involved_ids]


def _dates_text(lines):
    dates = sorted({line.start_date for line in lines})
    if len(dates) == 1:
        return f"{dates[0]:%b %d, %Y}"
    return f"{dates[0]:%b %d} – {dates[-1]:%b %d, %Y}"


def notify_filed(batch, new_employee_ids=(), resubmitted=False, edited=False):
    filer = getattr(batch.filed_by, "employee", None)
    by = filer.full_name if filer else batch.filed_by.username
    lines = list(batch.active_lines())
    for emp in {line.employee for line in lines if line.employee_id in new_employee_ids}:
        mine = [line for line in lines if line.employee_id == emp.pk]
        notify(emp, f"{by} filed a {batch.type_label} request for you ({_dates_text(mine)}).", "/official-requests/mine/")
    if batch.request_type == R.OT_RESTDAY_HOLIDAY:
        _notify_covering_supervisors(batch, lines, new_employee_ids, by)
    verb = "edited" if edited else ("resubmitted" if resubmitted else "filed")
    notify(_next_actors(batch), f"{by} {verb} a group {batch.type_label} request ({len(lines)} line(s)) "
                                f"— awaiting your action.", _url(batch))


def _notify_covering_supervisors(batch, lines, new_employee_ids, by):
    """D5: the employee's own next-level endorser (unit head, else section
    head) is told, for information only (never the filer). Narrowed from
    every covering Supervisor by the owner, 2026-11-03 (dry-run item 4)."""
    filer_pk = getattr(getattr(batch.filed_by, "employee", None), "pk", None)
    for emp in {line.employee for line in lines if line.employee_id in new_employee_ids}:
        mine = [line for line in lines if line.employee_id == emp.pk]
        others = [s for s in endorsers_for([emp]) if s.pk != filer_pk]
        notify(others, f"For your information: {by} put {emp.full_name} on a {batch.type_label} request "
                       f"({_dates_text(mine)}). No action needed from you.", _url(batch))


def notify_step(batch, action, notes):
    lines = list(batch.active_lines())
    if batch.status == R.RETURNED:
        remark = f' Remark: "{notes}"' if notes else ""
        notify(getattr(batch.filed_by, "employee", None),
               f"Your group {batch.type_label} request #{batch.pk} was returned.{remark} Please edit and resubmit.",
               _url(batch))
    elif batch.status in (R.APPROVED, R.REJECTED):
        word = batch.get_status_display().lower()
        if batch.status == R.REJECTED and notes:
            word = f'{word}. Remark: "{notes}"'  # owner request 2026-11-03
        for emp in {line.employee for line in lines}:
            mine = [line for line in lines if line.employee_id == emp.pk]
            notify(emp, f"The {batch.type_label} request filed for you ({_dates_text(mine)}) was {word}.",
                   "/official-requests/mine/")
        notify(getattr(batch.filed_by, "employee", None),
               f"Your group {batch.type_label} request #{batch.pk} was {word}.", _url(batch))
    else:
        notify(_next_actors(batch), f"A group {batch.type_label} request ({len(lines)} line(s)) is awaiting your action.",
               _url(batch))
