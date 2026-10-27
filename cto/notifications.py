"""Mirrors leave/notifications.py's shape — see that module's docstring
for the CLAUDE.md §11/§2 in-app-only-for-now rationale. CTO usage has no
data-entry-only shortcut (unlike leave), so there's one chain, not two."""

from leave.permissions import ao_step_actors, coh_step_actors, endorsers_for, hr_step_actors, second_endorsers_for
from notifications.services import notify

from .models import CTOUsageApplication

_QUEUE_URL = "/cto/queue/"
_MINE_URL = "/cto/mine/"

_OUTCOME_STATUSES = {
    CTOUsageApplication.APPROVED,
    CTOUsageApplication.REJECTED,
    CTOUsageApplication.RETURNED,
}


def notify_status_change(application):
    status = application.status
    filers = [application.employee]

    if status == CTOUsageApplication.SUBMITTED:
        notify(
            endorsers_for(filers),
            f"{application.employee} filed a CTO usage application — awaiting your endorsement.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.ENDORSED_BY_UNIT_HEAD:
        notify(
            second_endorsers_for(filers),
            f"A CTO usage application for {application.employee} was endorsed by the unit head — "
            "awaiting your endorsement.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.ENDORSED_BY_SUPERVISOR:
        notify(
            hr_step_actors(filers),
            f"A CTO usage application for {application.employee} is ready for HR processing.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.PROCESSED_BY_HR:
        notify(
            ao_step_actors(filers),
            f"A CTO usage application for {application.employee} is ready for AO recommendation.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.RECOMMENDED_BY_AO:
        notify(
            coh_step_actors(filers),
            f"A CTO usage application for {application.employee} is awaiting your approval.",
            _QUEUE_URL,
        )
    elif status in _OUTCOME_STATUSES:
        notify(
            application.employee,
            f"Your CTO usage application was {application.get_status_display().lower()}.",
            _MINE_URL,
        )


def notify_credit_entry(entry):
    """Sent when a CTO claim is submitted and credited (cto.claims.
    credit_claim) — there's no routing chain to notify along, just the
    one employee who just got credited."""
    notify(
        entry.employee,
        f"You were credited {entry.credited_days} CTO day(s) for {entry.get_duty_type_display()} on {entry.work_date}.",
        _MINE_URL,
    )
