"""Mirrors leave/notifications.py's shape (see that module's docstring for
the CLAUDE.md §11/§2 in-app-only-for-now rationale), but dispatches on
routing.next_status() rather than the raw status value — the same way
permissions.actor_may_advance() does — because Official Time and Travel
each skip one step (§6.2), so a saved status alone doesn't say who acts
next; routing.py already encodes that."""

from leave.permissions import remark_suffix, ao_step_actors, coh_step_actors, endorsers_for, hr_step_actors, second_endorsers_for
from notifications.services import notify

from .models import OfficialRequest
from .routing import approver_role, next_status_for


def notify_status_change(official_request):
    target_status = next_status_for(official_request)
    type_name = official_request.get_request_type_display()
    queue_url = "/official-requests/queue/"
    mine_url = "/official-requests/mine/"
    filers = [official_request.employee]

    if target_status in (OfficialRequest.ENDORSED_BY_UNIT_HEAD, OfficialRequest.ENDORSED_BY_SUPERVISOR):
        after_unit = official_request.status == OfficialRequest.ENDORSED_BY_UNIT_HEAD
        notify(
            second_endorsers_for(filers) if after_unit else endorsers_for(filers),
            f"{official_request.employee} filed a {type_name} request — awaiting your endorsement.",
            queue_url,
        )
    elif target_status == OfficialRequest.RECORDED:
        notify(
            hr_step_actors(filers, official_request),
            f"{official_request.employee} filed a {type_name} request for recording (approval is outside BDH).",
            queue_url,
        )
    elif target_status == OfficialRequest.PROCESSED_BY_HR:
        notify(
            hr_step_actors(filers, official_request),
            f"A {type_name} request for {official_request.employee} is ready for HR processing.",
            queue_url,
        )
    elif target_status == OfficialRequest.RECOMMENDED_BY_AO:
        notify(
            ao_step_actors(filers),
            f"A {type_name} request for {official_request.employee} is ready for AO recommendation.",
            queue_url,
        )
    elif target_status == OfficialRequest.FORWARDED_TO_CAPITOL:
        notify(
            coh_step_actors(filers),
            f"A Travel Order for {official_request.employee} is awaiting your recommending approval.",
            queue_url,
        )
    elif target_status == OfficialRequest.APPROVED:
        role = approver_role(official_request.request_type, target_status)
        if role == "HR":
            # Travel Order forwarded to the Capitol (owner decision 2026-11-03).
            notify(
                official_request.employee,
                f"Your {type_name} request was recommended by the Chief of Hospital and forwarded to the "
                "Capitol for the Governor's approval.",
                mine_url,
            )
            notify(
                hr_step_actors(filers, official_request),
                f"A Travel Order for {official_request.employee} is at the Capitol. Print it for signing, then "
                "record the Governor's decision.",
                queue_url,
            )
        else:
            notify(
                ao_step_actors(filers) if role == "AO" else coh_step_actors(filers),
                f"A {type_name} request for {official_request.employee} is awaiting your approval.",
                queue_url,
            )
    elif target_status is None:
        # Either just reached APPROVED (a terminal status with no further
        # step), or was REJECTED/RETURNED (not part of the chain at all) —
        # either way, the employee is the one who needs to know.
        notify(
            official_request.employee,
            f"Your {type_name} request was {official_request.status_label.lower()}.{remark_suffix(official_request)}",
            mine_url,
        )
