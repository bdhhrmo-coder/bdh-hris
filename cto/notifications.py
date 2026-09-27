"""Mirrors leave/notifications.py's shape — see that module's docstring
for the CLAUDE.md §11/§2 in-app-only-for-now rationale. CTO usage has no
data-entry-only shortcut (unlike leave), so there's one chain, not two."""

from accounts.models import RoleAssignment
from leave.permissions import employees_with_role, hr_employees, supervisors_of
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

    if status == CTOUsageApplication.SUBMITTED:
        notify(
            supervisors_of(application.employee),
            f"{application.employee} filed a CTO usage application — awaiting your endorsement.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.ENDORSED_BY_SUPERVISOR:
        notify(
            hr_employees(),
            f"A CTO usage application for {application.employee} is ready for HR processing.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.PROCESSED_BY_HR:
        notify(
            employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER),
            f"A CTO usage application for {application.employee} is ready for AO recommendation.",
            _QUEUE_URL,
        )
    elif status == CTOUsageApplication.RECOMMENDED_BY_AO:
        notify(
            employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL),
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
    """HR's direct-entry screen (credit_entry_create) has no routing chain
    to notify along — just the one employee who just got credited."""
    notify(
        entry.employee,
        f"You were credited {entry.credited_days} CTO day(s) for {entry.get_duty_type_display()} on {entry.work_date}.",
        _MINE_URL,
    )
