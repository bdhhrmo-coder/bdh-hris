"""
CLAUDE.md §11: in-app notifications only for now (email is deferred until
BDH's GovMail SMTP setup is confirmed — see CLAUDE.md §2, notifications
app docstring). One function decides who needs to know about a leave
application's current status, reusing the shared role-lookup helpers in
leave.permissions rather than hardcoding a second copy of "who is
Supervisor/HR/AO/COH for this employee". Called once, right after every
status-changing save — the same point every existing view already calls
LeaveApplicationAction.objects.create() from.
"""

from accounts.models import RoleAssignment
from notifications.services import notify

from .models import LeaveApplication
from .permissions import employees_with_role, hr_employees, supervisors_of

_QUEUE_URL = "/leave/queue/"
_MINE_URL = "/leave/mine/"

_OUTCOME_STATUSES = {
    LeaveApplication.APPROVED,
    LeaveApplication.RECORDED,
    LeaveApplication.REJECTED,
    LeaveApplication.RETURNED,
}


def notify_status_change(application):
    status = application.status
    leave_type_name = application.leave_type.name

    if status == LeaveApplication.SUBMITTED:
        if application.leave_type.requires_full_routing:
            notify(
                supervisors_of(application.employee),
                f"{application.employee} filed a {leave_type_name} application — awaiting your endorsement.",
                _QUEUE_URL,
            )
        else:
            # Regular Leave etc. (§6.2 data-entry-only path) goes straight to HR.
            notify(
                hr_employees(),
                f"{application.employee} filed a {leave_type_name} application for recording.",
                _QUEUE_URL,
            )
    elif status == LeaveApplication.ENDORSED_BY_SUPERVISOR:
        notify(
            hr_employees(),
            f"A {leave_type_name} application for {application.employee} is ready for HR processing.",
            _QUEUE_URL,
        )
    elif status == LeaveApplication.PROCESSED_BY_HR:
        notify(
            employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER),
            f"A {leave_type_name} application for {application.employee} is ready for AO recommendation.",
            _QUEUE_URL,
        )
    elif status == LeaveApplication.RECOMMENDED_BY_AO:
        notify(
            employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL),
            f"A {leave_type_name} application for {application.employee} is awaiting your approval.",
            _QUEUE_URL,
        )
    elif status in _OUTCOME_STATUSES:
        notify(
            application.employee,
            f"Your {leave_type_name} application was {application.get_status_display().lower()}.",
            _MINE_URL,
        )
