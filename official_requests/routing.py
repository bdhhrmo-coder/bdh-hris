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
    OR.OFFICIAL_TIME: dict(skip_supervisor=True, skip_hr=False),
    OR.TRAVEL: dict(skip_supervisor=False, skip_hr=True),
    OR.OT_RESTDAY_HOLIDAY: dict(skip_supervisor=False, skip_hr=False),
}

# Which role acts to reach a given status, and the action-name posted for it.
STEP_META = {
    OR.ENDORSED_BY_SUPERVISOR: "endorse",
    OR.PROCESSED_BY_HR: "process",
    OR.RECOMMENDED_BY_AO: "recommend",
    OR.APPROVED: "approve",
}


def routing_steps(request_type):
    """Ordered list of statuses this request_type passes through, e.g.
    [SUBMITTED, ENDORSED_BY_SUPERVISOR, PROCESSED_BY_HR, RECOMMENDED_BY_AO, APPROVED]
    for Official Business, with ENDORSED_BY_SUPERVISOR or PROCESSED_BY_HR
    omitted for a type that skips that step."""
    config = ROUTING_CONFIG[request_type]
    steps = [OR.SUBMITTED]
    if not config["skip_supervisor"]:
        steps.append(OR.ENDORSED_BY_SUPERVISOR)
    if not config["skip_hr"]:
        steps.append(OR.PROCESSED_BY_HR)
    steps.append(OR.RECOMMENDED_BY_AO)
    steps.append(OR.APPROVED)
    return steps


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


def action_name_for(status):
    return STEP_META.get(status)
