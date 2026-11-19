"""
Who acts on a pass, and what their action does (CLAUDE.md 13B).

  Ordinary pass:  Employee -> Supervisor endorses -> AO or COH approves
  Third pass, exception, emergency pass: Employee -> Supervisor endorses
                  -> COH alone decides
  COH's own pass: Employee (COH) -> HR records it (owner decision
                  2026-11-14, Option B)

No HRMO step. The Supervisor step is one level above the filer, the same
helper every other request uses (leave.permissions.endorsers_for): the unit
head, else the section head, else the AO; the COH for the AO's own pass.
Single level only (owner decision 2026-11-14).

When the person at the Supervisor step may also approve this pass (the AO
for a section head's ordinary pass, the COH for the AO's own pass), their
action approves it: they are both the next level up and the approver, so a
second step would only ask them twice. Nobody acts on their own pass, and
whoever endorsed is not the approver.
"""

from accounts.models import RoleAssignment
from leave.permissions import coh_step_actors, employees_with_role, endorsers_for, hr_step_actors

from .models import OutOnPass as P

ENDORSE = "endorse"
APPROVE = "approve"
RECORD = "record"
RETURN = "return"
REJECT = "reject"
CANCEL = "cancel"

ACTION_LABELS = {ENDORSE: "Endorse", APPROVE: "Approve", RECORD: "Record", RETURN: "Return",
                 REJECT: "Reject", CANCEL: "Cancel"}


def _ids(people):
    return {e.pk for e in people}


def endorser_ids(p):
    """Who endorsed the pass since it was last (re)submitted."""
    ids = set()
    for a in p.actions.select_related("acted_by__employee").order_by("acted_at", "pk"):
        if a.action in ("submit", "resubmit"):
            ids = set()
        elif a.resulting_status == P.ENDORSED:
            emp = getattr(a.acted_by, "employee", None)
            if emp is not None:
                ids.add(emp.pk)
    return ids


def approvers(p):
    """Who may give the final decision, before leaving out the endorser."""
    filers = [p.employee]
    if p.is_special:
        return coh_step_actors(filers)
    out = {}
    for role in (RoleAssignment.ADMINISTRATIVE_OFFICER, RoleAssignment.CHIEF_OF_HOSPITAL):
        for e in employees_with_role(role):
            if e.pk != p.employee_id:
                out[e.pk] = e
    return list(out.values())


def step_actors(p):
    """Who may act on the pass now (empty when nothing is waiting)."""
    filers = [p.employee]
    if p.status == P.SUBMITTED:
        if p.recorded_only:
            return hr_step_actors(filers)
        return endorsers_for(filers)
    if p.status == P.ENDORSED:
        done = endorser_ids(p)
        return [e for e in approvers(p) if e.pk not in done]
    return []


def can_act(acting_employee, p):
    return acting_employee is not None and acting_employee.pk in _ids(step_actors(p))


def forward_action(acting_employee, p):
    """The forward action this person's step takes: endorse, approve or record."""
    if p.status == P.SUBMITTED and p.recorded_only:
        return RECORD
    if p.status == P.SUBMITTED:
        return APPROVE if acting_employee.pk in _ids(approvers(p)) else ENDORSE
    return APPROVE


def is_supervisor_step(p):
    return p.status == P.SUBMITTED and not p.recorded_only


def result_of(action, p):
    return {ENDORSE: P.ENDORSED, APPROVE: P.APPROVED, RECORD: P.RECORDED,
            RETURN: P.RETURNED, REJECT: P.REJECTED, CANCEL: P.CANCELLED}[action]


def allowed_actions(acting_employee, p):
    """Buttons for this person on this pass."""
    if not can_act(acting_employee, p):
        return []
    actions = [forward_action(acting_employee, p), RETURN]
    if not p.recorded_only:  # HR records the COH's pass; it may return it, not reject it
        actions.append(REJECT)
    return actions


def can_cancel(acting_employee, p):
    """The filer may withdraw a pass that is still live and not yet used."""
    return (acting_employee is not None and acting_employee.pk == p.employee_id
            and p.status in P.LIVE_STATUSES and p.tapped_out_at is None)


def can_edit(acting_employee, p):
    """A returned pass is corrected and resubmitted by the filer."""
    return acting_employee is not None and acting_employee.pk == p.employee_id and p.status == P.RETURNED


def waiting_for(acting_employee):
    """Passes waiting for this person's action, oldest first."""
    if acting_employee is None:
        return []
    pending = (P.objects.filter(status__in=P.PENDING_STATUSES).exclude(employee=acting_employee)
               .select_related("employee").order_by("filed_at", "pk"))
    return [p for p in pending if can_act(acting_employee, p)]


def may_view_proof(acting_employee, p):
    """Proof is confidential (CLAUDE.md 13B): the filer, the Supervisor step
    and the approver only - not HR, unless HR is one of them."""
    if acting_employee is None:
        return False
    if acting_employee.pk == p.employee_id:
        return True
    acted = {a.acted_by_id for a in p.actions.all()}
    if acting_employee.user_id in acted:
        return True
    return acting_employee.pk in _ids(endorsers_for([p.employee])) | _ids(approvers(p))
