"""
Group (batch) filing of Travel, Official Business and Authorized OT /
rest-day / holiday work - Batch 2, Item 7 (owner decisions 2026-10-07).

WHO MAY FILE
  Supervisor        - only employees they supervise (incl. as OIC); every type.
  HR Processor      - any employee; Travel and Official Business ONLY.
  HR Administrator  - any employee; every type.
  Someone holding several of these roles files under the highest one that
  allows the chosen type: HR Administrator, then HR Processor, then
  Supervisor.

ROUTE (the filer's own step counts as done; the whole batch moves together)
  OT / rest day / holiday   by Supervisor: HR -> AO -> COH
                            by HR Admin:   AO -> COH
  Travel                    by Supervisor: AO -> COH
                            by HR:         AO -> COH
  Official Business         by Supervisor: HR -> AO -> COH
                            by HR:         Supervisor -> AO -> COH
                            (the Supervisor still endorses - owner decision)
  A regular employee filing their own request keeps the normal route
  (single request form, routing.py) - nothing here changes that.

The filer can never act on their own batch at a later step, whatever other
roles (or OIC) they hold. Approving an OT batch creates NO CTO credit: HR
still files each CTO claim from the approved line (CLAUDE.md §7.1).
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
    employees_with_role,
    hr_employees,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
    is_supervisor_of,
    supervisors_of,
)
from notifications.services import notify

from .models import OfficialRequest as R
from .models import OfficialRequestAction, OfficialRequestBatch as B, OfficialRequestBatchAction

MAX_DATE_LINES = 31
MAX_ENTRIES = 300

# kind code -> (label, request_type, work_kind)
KINDS = {
    "TRAVEL": ("Travel", R.TRAVEL, ""),
    "OFFICIAL_BUSINESS": ("Official Business", R.OFFICIAL_BUSINESS, ""),
    "OT": ("Authorized overtime", R.OT_RESTDAY_HOLIDAY, B.WORK_OT),
    "REST_DAY": ("Rest-day work", R.OT_RESTDAY_HOLIDAY, B.WORK_REST_DAY),
    "HOLIDAY": ("Holiday work", R.OT_RESTDAY_HOLIDAY, B.WORK_HOLIDAY),
}
WORK_KINDS = {"OT", "REST_DAY", "HOLIDAY"}

KINDS_BY_ROLE = {
    B.FILER_HR_ADMINISTRATOR: set(KINDS),
    B.FILER_HR_PROCESSOR: {"TRAVEL", "OFFICIAL_BUSINESS"},
    B.FILER_SUPERVISOR: set(KINDS),
}
ROLE_PRIORITY = [B.FILER_HR_ADMINISTRATOR, B.FILER_HR_PROCESSOR, B.FILER_SUPERVISOR]

HR_ROLES = {B.FILER_HR_PROCESSOR, B.FILER_HR_ADMINISTRATOR}


def _route_key(request_type, filer_role):
    return request_type, ("HR" if filer_role in HR_ROLES else "SUPERVISOR")


ROUTES = {
    (R.OT_RESTDAY_HOLIDAY, "SUPERVISOR"): [R.PROCESSED_BY_HR, R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.OT_RESTDAY_HOLIDAY, "HR"): [R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.TRAVEL, "SUPERVISOR"): [R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.TRAVEL, "HR"): [R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.OFFICIAL_BUSINESS, "SUPERVISOR"): [R.PROCESSED_BY_HR, R.RECOMMENDED_BY_AO, R.APPROVED],
    (R.OFFICIAL_BUSINESS, "HR"): [R.ENDORSED_BY_SUPERVISOR, R.RECOMMENDED_BY_AO, R.APPROVED],
}
# Steps of the normal single-request route that the batch route leaves out.
SKIPPED = {
    (R.OT_RESTDAY_HOLIDAY, "SUPERVISOR"): "Supervisor endorsement",
    (R.OT_RESTDAY_HOLIDAY, "HR"): "Supervisor endorsement and HR processing",
    (R.TRAVEL, "SUPERVISOR"): "Supervisor endorsement",
    (R.TRAVEL, "HR"): "Supervisor endorsement",
    (R.OFFICIAL_BUSINESS, "SUPERVISOR"): "Supervisor endorsement",
    (R.OFFICIAL_BUSINESS, "HR"): "HR processing",
}
STEP_ACTIONS = {
    R.ENDORSED_BY_SUPERVISOR: ("endorse", "Endorse", "Supervisor"),
    R.PROCESSED_BY_HR: ("process", "Process", "HR"),
    R.RECOMMENDED_BY_AO: ("recommend", "Recommend", "Administrative Officer"),
    R.APPROVED: ("approve", "Approve", "Chief of Hospital"),
}


# -- who may file -------------------------------------------------------------

def filer_roles(acting_employee):
    if acting_employee is None:
        return []
    held = {
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


def filing_role_for(acting_employee, kind):
    for role in filer_roles(acting_employee):
        if kind in KINDS_BY_ROLE[role]:
            return role
    return None


def supervised_employees(acting_employee):
    """Active employees this Supervisor (or OIC) covers."""
    qs = Employee.objects.filter(is_active=True)
    section_ids, unit_ids = set(), set()
    for ra in acting_employee.active_role_assignments().filter(role=RoleAssignment.SUPERVISOR):
        if ra.section_id is None and ra.unit_id is None:
            return qs  # hospital-wide assignment
        if ra.section_id:
            section_ids.add(ra.section_id)
        if ra.unit_id:
            unit_ids.add(ra.unit_id)
    from django.db.models import Q

    return qs.filter(Q(sections__in=section_ids) | Q(units__in=unit_ids)).distinct()


def employees_for(acting_employee, filer_role):
    if filer_role in HR_ROLES:
        return Employee.objects.filter(is_active=True)
    if filer_role == B.FILER_SUPERVISOR:
        return supervised_employees(acting_employee)
    return Employee.objects.none()


def pickable_employees(acting_employee):
    """Everyone this person could include under any of their filing roles
    (the form narrows it again once the type is chosen)."""
    roles = filer_roles(acting_employee)
    if any(r in HR_ROLES for r in roles):
        return Employee.objects.filter(is_active=True)
    if B.FILER_SUPERVISOR in roles:
        return supervised_employees(acting_employee)
    return Employee.objects.none()


def common_supervisors(employees, exclude_employee=None):
    """Supervisors who cover EVERY employee given (for the OB-by-HR
    Supervisor endorsement step)."""
    common = None
    for emp in employees:
        ids = {s.pk for s in supervisors_of(emp)}
        common = ids if common is None else common & ids
        if not common:
            return []
    if exclude_employee is not None and common:
        common.discard(exclude_employee.pk)
    return list(Employee.objects.filter(pk__in=common or []))


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
    return ["Filed by " + batch.get_filer_role_display()] + [STEP_ACTIONS[s][2] for s in route(batch)]


def may_act(acting_employee, user, batch):
    if acting_employee is None or batch.filed_by_id == user.pk:
        return False  # the filer never acts on their own batch again
    target = next_status(batch)
    if target == R.ENDORSED_BY_SUPERVISOR:
        lines = list(batch.active_lines())
        return bool(lines) and all(is_supervisor_of(acting_employee, line.employee) for line in lines)
    if target == R.PROCESSED_BY_HR:
        return is_hr(acting_employee)
    if target == R.RECOMMENDED_BY_AO:
        return is_administrative_officer(acting_employee)
    if target == R.APPROVED:
        return is_chief_of_hospital(acting_employee)
    return False


def available_actions(acting_employee, user, batch):
    if not may_act(acting_employee, user, batch):
        return []
    code, label, _ = STEP_ACTIONS[next_status(batch)]
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
        purpose=batch.purpose, is_restday_or_holiday=restday,
        hours_requested=hours_between(entry.get("time_from"), entry.get("time_to")) if is_ot else None,
        line_note=entry.get("note", "")[:255], status=batch.status,
    )


def filing_note(batch, count):
    role = batch.get_filer_role_display()
    return (f"Filed on behalf of {count} employee-date line(s) by {role}. "
            f"Skipped step: {skipped_steps(batch)} (filed on behalf by {role}).")


@transaction.atomic
def create_batch(user, filer_role, kind, purpose, destination, entries):
    _, request_type, work_kind = KINDS[kind]
    batch = B.objects.create(request_type=request_type, work_kind=work_kind, purpose=purpose,
                             destination=destination if request_type == R.TRAVEL else "",
                             filed_by=user, filer_role=filer_role)
    lines = [R.objects.create(batch=batch, **_line_values(batch, e)) for e in entries]
    _log(batch, "submit", user, filing_note(batch, len(lines)), lines)
    notify_filed(batch, new_employee_ids={e["employee"].pk for e in entries})
    return batch


@transaction.atomic
def resubmit_batch(batch, user, purpose, destination, entries, note=""):
    """The filer's edit after a return: same batch, lines updated/added,
    removed lines cancelled (kept for the audit trail). Starts again from
    the first step of the route."""
    batch.purpose = purpose
    batch.destination = destination if batch.request_type == R.TRAVEL else ""
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
                                             notes=f"Batch #{batch.pk}: removed by the filer after a return.",
                                             acted_by=user)
    _log(batch, "resubmit", user, note or "Edited and resubmitted after a return.")
    notify_filed(batch, new_employee_ids={k[0] for k in keep - before}, resubmitted=True)
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
    filer = getattr(batch.filed_by, "employee", None)
    if target == R.ENDORSED_BY_SUPERVISOR:
        people = common_supervisors([line.employee for line in batch.active_lines()])
    elif target == R.PROCESSED_BY_HR:
        people = list(hr_employees())
    elif target == R.RECOMMENDED_BY_AO:
        people = list(employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER))
    elif target == R.APPROVED:
        people = list(employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL))
    else:
        people = []
    return [p for p in people if filer is None or p.pk != filer.pk]


def _dates_text(lines):
    dates = sorted({line.start_date for line in lines})
    if len(dates) == 1:
        return f"{dates[0]:%b %d, %Y}"
    return f"{dates[0]:%b %d} – {dates[-1]:%b %d, %Y}"


def notify_filed(batch, new_employee_ids=(), resubmitted=False):
    filer = getattr(batch.filed_by, "employee", None)
    by = filer.full_name if filer else batch.filed_by.username
    lines = list(batch.active_lines())
    for emp in {line.employee for line in lines if line.employee_id in new_employee_ids}:
        mine = [line for line in lines if line.employee_id == emp.pk]
        notify(emp, f"{by} filed a {batch.type_label} request for you ({_dates_text(mine)}).", "/official-requests/mine/")
    verb = "resubmitted" if resubmitted else "filed"
    notify(_next_actors(batch), f"{by} {verb} a group {batch.type_label} request ({len(lines)} line(s)) "
                                f"— awaiting your action.", _url(batch))


def notify_step(batch, action, notes):
    lines = list(batch.active_lines())
    if batch.status == R.RETURNED:
        remark = f' Remark: "{notes}"' if notes else ""
        notify(getattr(batch.filed_by, "employee", None),
               f"Your group {batch.type_label} request #{batch.pk} was returned.{remark} Please edit and resubmit.",
               _url(batch))
    elif batch.status in (R.APPROVED, R.REJECTED):
        word = batch.get_status_display().lower()
        for emp in {line.employee for line in lines}:
            mine = [line for line in lines if line.employee_id == emp.pk]
            notify(emp, f"The {batch.type_label} request filed for you ({_dates_text(mine)}) was {word}.",
                   "/official-requests/mine/")
        notify(getattr(batch.filed_by, "employee", None),
               f"Your group {batch.type_label} request #{batch.pk} was {word}.", _url(batch))
    else:
        notify(_next_actors(batch), f"A group {batch.type_label} request ({len(lines)} line(s)) is awaiting your action.",
               _url(batch))
