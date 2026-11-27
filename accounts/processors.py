"""Part A (owner decisions 2026-11-27): who processed each step, shown on the
request pages in short form ("J. Dela Cruz"). Display only: routing, rules,
notifications, printed forms and the Audit Log (full names) are unchanged.

Rules:
- Every name is shown as first initial + surname (no middle name, no Jr./III).
- The AO and the COH acting personally are never named on these displays;
  their step shows only its label and date, as before.
- An OIC is shown as "M. Cruz - acting as OIC for L. Ramos" (Supervisor
  steps) or "... - acting as OIC for AO" / "... for COH".
- Where the record does not clearly show an OIC acted at an AO or COH step,
  no name is shown there (never a guess).

The capacity is worked out once, when the action is saved, and stored on the
action row (`acting_capacity`, `acting_for`; decision 1, Option B). Rows saved
before this change are worked out the same way when they are displayed, from
the OIC role lines and their dates (no new data entry).
The Performance module is never touched here.
"""

from django.utils import timezone

# Stored capacities
SELF = "SELF"            # show the person's own short name
HIDDEN = "HIDDEN"        # the AO or COH acting personally, or unclear at an AO/COH step
OIC_SUPERVISOR = "OIC_SUP"
OIC_AO = "OIC_AO"
OIC_COH = "OIC_COH"
CAPACITY_CHOICES = [(SELF, "Own capacity"), (HIDDEN, "AO / COH (not named)"),
                    (OIC_SUPERVISOR, "OIC for a Supervisor"), (OIC_AO, "OIC for the AO"),
                    (OIC_COH, "OIC for the COH")]

# Which office acts at a step, from the status the action moved the request
# into. "ANY" = the Supervisor step or an unclear one (worked out from the
# person's own lines). AO_COH = the last step, done by the AO or the COH.
SUP, HR, AO, COH, AO_COH, ANY, PERSON = "SUP", "HR", "AO", "COH", "AO_COH", "ANY", "PERSON"
_FORWARD = {
    "ENDORSED_BY_UNIT_HEAD": "SUP_UNIT", "ENDORSED_BY_ONE_UNIT_HEAD": "SUP_UNIT", "ENDORSED_BY_SUPERVISOR": SUP,
    "ENDORSED": SUP, "CONFIRMED": SUP,
    "PROCESSED_BY_HR": HR, "REVIEWED": HR, "RECORDED": HR,
    "RECOMMENDED_BY_AO": AO, "RECOMMENDED": AO,
    "FOR_CAPITOL_APPROVAL": COH, "FORWARDED_TO_CAPITOL": COH,
    "APPROVED": AO_COH, "APPLIED": AO_COH,
    "VALIDATED": PERSON, "VERIFIED": PERSON,  # ICTU / HR validator; guard
}
# For a return or rejection: the step that was attempted, from the status before it.
_NEXT_AFTER = {
    "ENDORSED_BY_UNIT_HEAD": SUP, "PROCESSED_BY_HR": AO, "RECOMMENDED_BY_AO": COH,
    "REVIEWED": AO, "RECOMMENDED": COH, "ENDORSED": AO_COH,
}
STOP_ACTIONS = {"return", "reject"}
# The filer's own actions, and HR recording on someone's behalf: name as is.
FILER_ACTIONS = {"submit", "resubmit", "file", "edit", "cancel", "consent", "decline", "tap_out", "tap_in",
                 "add_line", "remove_line"}
HR_ACTIONS = {"capitol_return", "capitol_reject", "record_disapproval", "hr_close", "hr_not_used",
              "correct_day_type", "opening_line_added", "opening_line_voided", "record", "process", "review_hr"}
# Shown as "Recorded by <name>" (owner-approved A6 scope, 2026-11-27).
RECORDED_BY_ACTIONS = {"capitol_return", "capitol_reject", "record_disapproval", "hr_close", "hr_not_used",
                       "correct_day_type", "opening_line_added", "opening_line_voided"}

_CLAIM_STEP_OFFICE = {"SUPERVISOR": SUP, "CHIEF_NURSE": SUP, "HR": HR, "AO": AO, "COH": COH}


def short_name(employee):
    """First initial + surname, e.g. "J. Dela Cruz". Surname only when there
    is no first name. Middle names and suffixes are left out."""
    if employee is None:
        return ""
    first = (employee.first_name or "").strip()
    last = (employee.surname or "").strip()
    if not first:
        return last
    return f"{first[0].upper()}. {last}" if last else f"{first[0].upper()}."


def user_short_name(user):
    if user is None:
        return "System"
    emp = getattr(user, "employee", None)
    if emp is not None:
        return short_name(emp)
    return user.get_full_name() or user.username


# --- subjects of a request (for "does this Supervisor line cover it?") ------

def _parent(row):
    for attr in ("application", "claim", "request", "batch", "out_on_pass", "missed_tap", "schedule"):
        obj = getattr(row, attr, None) if hasattr(row, attr + "_id") else None
        if obj is not None:
            return obj
    return None


def subjects(row):
    """The employees the request is about."""
    p = _parent(row)
    if p is None:
        return []
    if hasattr(p, "employee_a_id"):
        return [e for e in (p.employee_a, p.employee_b) if e is not None]
    if hasattr(p, "out_on_pass_id") and not hasattr(p, "employee_id"):
        return [p.out_on_pass.employee]
    if hasattr(p, "lines") and hasattr(p, "filer_role"):  # group request
        return [line.employee for line in p.lines.select_related("employee")]
    emp = getattr(p, "employee", None)
    return [emp] if emp is not None else []


def _prev_status(row):
    p = _parent(row)
    if p is None or not hasattr(row, "resulting_status"):
        return None
    qs = p.actions.all()
    if row.pk:
        qs = qs.filter(pk__lt=row.pk)
    last = qs.order_by("-pk").first()
    return getattr(last, "resulting_status", None) if last else "SUBMITTED"


def office_for(row):
    action = getattr(row, "action", "")
    if action in FILER_ACTIONS:
        return PERSON
    if action in HR_ACTIONS:
        return HR
    step = getattr(row, "step", None)  # CTO claims keep the step on the row
    if step:
        return _CLAIM_STEP_OFFICE.get(step, ANY)
    if action in STOP_ACTIONS:
        return _NEXT_AFTER.get(_prev_status(row) or "", ANY)
    status = getattr(row, "resulting_status", "")
    if status in ("APPROVED", "APPLIED") and _prev_status(row) in ("RECOMMENDED_BY_AO", "RECOMMENDED"):
        return COH  # the step after the AO's recommendation is the COH's
    return _FORWARD.get(status, ANY)


# --- the person's lines on the day they acted --------------------------------

def _lines(employee, day):
    from accounts.models import RoleAssignment as R

    return list(employee.role_assignments.filter(R.in_force_q(day)).select_related("delegated_by"))


def _covers(line, emp):
    if line.unit_id:
        return emp.units.filter(pk=line.unit_id).exists()
    if line.section_id:
        return emp.sections.filter(pk=line.section_id).exists()
    return False


def capacity(row, actor=None, when=None):
    """(capacity, acting_for employee or None) for one action row."""
    from accounts.models import RoleAssignment as R

    if actor is None:
        actor = getattr(getattr(row, "acted_by", None), "employee", None)
    if actor is None:
        return SELF, None
    office = office_for(row)
    if office in (PERSON, HR):
        return SELF, None
    day = timezone.localdate(when or getattr(row, "acted_at", None) or timezone.now())
    lines = _lines(actor, day)
    own = {ln.role for ln in lines if not ln.is_oic}
    oic = [ln for ln in lines if ln.is_oic]
    oic_ao = [ln for ln in oic if ln.role == R.ADMINISTRATIVE_OFFICER]
    oic_coh = [ln for ln in oic if ln.role == R.CHIEF_OF_HOSPITAL]

    if office in (AO, COH, AO_COH):
        own_ao, own_coh = R.ADMINISTRATIVE_OFFICER in own, R.CHIEF_OF_HOSPITAL in own
        # The office of this step first: the COH step is the COH's, or an OIC's for the COH
        # (the AO may be OIC for the COH, so holding the AO role says nothing there).
        order = ((own_coh, oic_coh, OIC_COH), (own_ao, oic_ao, OIC_AO)) if office == COH else \
                ((own_ao, oic_ao, OIC_AO), (own_coh, oic_coh, OIC_COH))
        if office == AO_COH:
            if own_ao or own_coh:
                return HIDDEN, None  # the AO or COH personally
            if not oic_ao and not oic_coh:
                return SELF, None  # another capacity (e.g. HR Administrator approving a minor correction)
            if bool(oic_ao) != bool(oic_coh):
                return (OIC_AO, oic_ao[0].delegated_by) if oic_ao else (OIC_COH, oic_coh[0].delegated_by)
            return HIDDEN, None  # OIC for both: not clear which office, so no name (A12)
        for holds_own, oic_lines, label in order:
            if holds_own:
                return HIDDEN, None  # the AO or COH personally
            if oic_lines:
                return label, oic_lines[0].delegated_by
        return SELF, None  # another capacity (e.g. HR)

    # Supervisor step (or not clear): the AO / COH personally first.
    if own & {R.ADMINISTRATIVE_OFFICER, R.CHIEF_OF_HOSPITAL}:
        return HIDDEN, None
    emps = subjects(row)
    if office == "SUP_UNIT":  # the unit-head step: only unit lines count
        lines = [ln for ln in lines if ln.role != R.SUPERVISOR or ln.unit_id]
        oic = [ln for ln in lines if ln.is_oic]
    sup_own = [ln for ln in lines if not ln.is_oic and ln.role == R.SUPERVISOR and any(_covers(ln, e) for e in emps)]
    if sup_own:
        return SELF, None
    sup_oic = [ln for ln in oic if ln.role == R.SUPERVISOR and any(_covers(ln, e) for e in emps)]
    delegators = {ln.delegated_by_id for ln in sup_oic}
    if len(delegators) == 1:
        return OIC_SUPERVISOR, sup_oic[0].delegated_by
    if sup_oic:
        return SELF, None  # several possible: name only, no label
    if office in (SUP, ANY):
        if bool(oic_ao) != bool(oic_coh):
            return (OIC_AO, oic_ao[0].delegated_by) if oic_ao else (OIC_COH, oic_coh[0].delegated_by)
    return SELF, None


def describe(row):
    """The text for the "By" column: '' (not named), a short name, a short
    name with its OIC label, or 'Recorded by <short name>'."""
    user = getattr(row, "acted_by", None)
    if user is None:
        return ""  # automatic step (no person recorded): nothing shown
    cap = getattr(row, "acting_capacity", "") or ""
    other = getattr(row, "acting_for", None) if cap else None
    if not cap:
        cap, other = capacity(row)
    name = user_short_name(user)
    if cap == HIDDEN:
        return ""
    if cap == OIC_AO:
        return f"{name} - acting as OIC for AO"
    if cap == OIC_COH:
        return f"{name} - acting as OIC for COH"
    if cap == OIC_SUPERVISOR:
        return f"{name} - acting as OIC for {short_name(other)}" if other else name
    action = getattr(row, "action", "")
    if action in RECORDED_BY_ACTIONS:
        return f"Recorded by {name}"
    if action in ("submit", "file"):
        actor = getattr(user, "employee", None)
        people = subjects(row)
        if actor is not None and people and actor not in people:  # entered for someone else
            return f"{'Filed' if hasattr(_parent(row), 'filer_role') else 'Recorded'} by {name}"
    return name


# --- storing it when an action is saved (decision 1, Option B) -------------

ACTION_MODELS = [
    "leave.LeaveApplicationAction", "cto.CTOUsageApplicationAction", "cto.CTOClaimAction",
    "exchange.DutyExchangeRequestAction", "attendance.AttendanceCorrectionRequestAction",
    "official_requests.OfficialRequestAction", "official_requests.OfficialRequestBatchAction",
    "datechange.DateChangeAction", "schedules.ScheduleAction", "outonpass.OutOnPassAction",
    "outonpass.MissedTapAction",
]


def store_capacity(sender, instance, **kwargs):
    if instance.pk or instance.acting_capacity:
        return
    try:
        cap, other = capacity(instance, when=timezone.now())
    except Exception:  # never block an approval because of a display detail
        return
    instance.acting_capacity = cap
    instance.acting_for = other


def connect():
    from django.apps import apps
    from django.db.models.signals import pre_save

    for label in ACTION_MODELS:
        pre_save.connect(store_capacity, sender=apps.get_model(label), dispatch_uid=f"partA-{label}")
