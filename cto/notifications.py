"""CTO usage application notifications (in-app). OT request and CTO claim
notifications are sent by cto/routing.py and cto/claims.py."""

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
