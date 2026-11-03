"""
Generic routing-step engine for OfficialRequest, since three of the four
request types share the identical chain and the other two each skip
exactly one step (see models.py's module docstring for the §6.2 chains).
Rather than hand-writing four near-identical branch trees (the pattern
used in leave/cto/exchange/attendance for their single fixed chain), this
computes the ordered step list per request_type from ROUTING_CONFIG, so
adding or re-shaping a request type later is a one-line config change,
not a new branch tree.
"""

from .models import OfficialRequest as OR

ROUTING_CONFIG = {
    OR.OFFICIAL_BUSINESS: dict(skip_supervisor=False, skip_hr=False),
    OR.OFFICIAL_TIME: dict(skip_supervisor=True, skip_hr=False),  # retired 2026-11-03; old records only
    OR.TRAVEL: dict(skip_supervisor=False, skip_hr=True),
    OR.OT_RESTDAY_HOLIDAY: dict(skip_supervisor=False, skip_hr=False),
}

# Which action name is posted to reach a given status. APPROVED differs by
# type, see action_name_for().
STEP_META = {
    OR.ENDORSED_BY_UNIT_HEAD: "endorse",
    OR.ENDORSED_BY_SUPERVISOR: "endorse",
    OR.PROCESSED_BY_HR: "process",
    OR.RECOMMENDED_BY_AO: "recommend",
    OR.FORWARDED_TO_CAPITOL: "forward",
    OR.APPROVED: "approve",
    OR.RECORDED: "record",
}

# Button wording for each action (the queue shows these).
ACTION_LABELS = {
    "endorse": "Endorse",
    "process": "Process",
    "recommend": "Recommend",
    "forward": "Recommend approval (forward to the Capitol)",
    "approve": "Approve",
    "record": "Record",
    "record_approval": "Record the Governor's approval",
}


def routing_steps(request_type):
    """Ordered list of statuses this request_type passes through.

    Owner decisions 2026-11-03:
      Official Business (Travel Order): Supervisor -> HR -> AO -> COH
        (recommending approval, FORWARDED_TO_CAPITOL) -> APPROVED, which HR
        records once the Governor has signed the Travel Order.
      Local Official Errand (TRAVEL): Supervisor -> AO, who approves (F59).
      OT / rest day / holiday: Supervisor -> HR -> AO -> COH (unchanged)."""
    config = ROUTING_CONFIG[request_type]
    steps = [OR.SUBMITTED]
    if not config["skip_supervisor"]:
        steps.append(OR.ENDORSED_BY_SUPERVISOR)
    if not config["skip_hr"]:
        steps.append(OR.PROCESSED_BY_HR)
    if request_type == OR.TRAVEL:
        steps.append(OR.APPROVED)  # approved by the AO
        return steps
    steps.append(OR.RECOMMENDED_BY_AO)
    if request_type == OR.OFFICIAL_BUSINESS:
        steps.append(OR.FORWARDED_TO_CAPITOL)
    steps.append(OR.APPROVED)
    return steps


def approver_role(request_type, target_status):
    """Who acts to reach target_status: "SUPERVISOR", "HR", "AO" or "COH"."""
    if target_status in (OR.ENDORSED_BY_UNIT_HEAD, OR.ENDORSED_BY_SUPERVISOR):
        return "SUPERVISOR"
    if target_status in (OR.PROCESSED_BY_HR, OR.RECORDED):
        return "HR"
    if target_status == OR.RECOMMENDED_BY_AO:
        return "AO"
    if target_status == OR.FORWARDED_TO_CAPITOL:
        return "COH"
    if target_status == OR.APPROVED:
        if request_type == OR.TRAVEL:
            return "AO"
        if request_type == OR.OFFICIAL_BUSINESS:
            return "HR"  # records the Governor's approval
        return "COH"
    return None


def next_status(request_type, current_status):
    """The status one step of progress from current_status for this
    request_type, or None if current_status is terminal or not part of
    this type's chain (shouldn't happen for a well-formed request)."""
    steps = routing_steps(request_type)
    if current_status not in steps:
        return None
    idx = steps.index(current_status)
    if idx + 1 >= len(steps):
        return None
    return steps[idx + 1]


def next_status_for(official_request):
    """next_status for this request: a COH's own request (recorded_only)
    goes SUBMITTED -> RECORDED by HR, nothing else (owner 2026-10-26)."""
    if official_request.recorded_only:
        return OR.RECORDED if official_request.status == OR.SUBMITTED else None
    # Two-level endorsement (owner decision 2026-10-27): unit head, then section head.
    if official_request.status == OR.ENDORSED_BY_UNIT_HEAD:
        return OR.ENDORSED_BY_SUPERVISOR
    nxt = next_status(official_request.request_type, official_request.status)
    if nxt == OR.ENDORSED_BY_SUPERVISOR and official_request.status == OR.SUBMITTED:
        from leave.permissions import needs_unit_step

        if needs_unit_step([official_request.employee]):
            return OR.ENDORSED_BY_UNIT_HEAD
    return nxt


def action_name_for(status, request_type=None):
    if status == OR.APPROVED and request_type == OR.OFFICIAL_BUSINESS:
        return "record_approval"
    return STEP_META.get(status)


def action_label(action):
    return ACTION_LABELS.get(action, (action or "").capitalize())
