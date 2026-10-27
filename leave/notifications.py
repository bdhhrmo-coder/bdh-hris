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

from notifications.services import notify

from .models import LeaveApplication
from .permissions import ao_step_actors, coh_step_actors, endorsers_for, hr_step_actors, second_endorsers_for

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
    filers = [application.employee]
    leave_type_name = application.leave_type.name

    if status == LeaveApplication.SUBMITTED:
        if application.leave_type.requires_full_routing:
            notify(
                endorsers_for(filers),
                f"{application.employee} filed a {leave_type_name} application — awaiting your endorsement.",
                _QUEUE_URL,
            )
        else:
            # Regular Leave etc. (§6.2 data-entry-only path) goes straight to HR.
            notify(
                hr_step_actors(filers),
                f"{application.employee} filed a {leave_type_name} application for recording.",
                _QUEUE_URL,
            )
    elif status == LeaveApplication.ENDORSED_BY_UNIT_HEAD:
        notify(
            second_endorsers_for(filers),
            f"A {leave_type_name} application for {application.employee} was endorsed by the unit head — "
            "awaiting your endorsement.",
            _QUEUE_URL,
        )
    elif status == LeaveApplication.ENDORSED_BY_SUPERVISOR:
        notify(
            hr_step_actors(filers),
            f"A {leave_type_name} application for {application.employee} is ready for HR processing.",
            _QUEUE_URL,
        )
    elif status == LeaveApplication.PROCESSED_BY_HR:
        notify(
            ao_step_actors(filers),
            f"A {leave_type_name} application for {application.employee} is ready for AO recommendation.",
            _QUEUE_URL,
        )
    elif status == LeaveApplication.RECOMMENDED_BY_AO:
        notify(
            coh_step_actors(filers),
            f"A {leave_type_name} application for {application.employee} is awaiting your approval.",
            _QUEUE_URL,
        )
    elif status in _OUTCOME_STATUSES:
        notify(
            application.employee,
            f"Your {leave_type_name} application was {application.get_status_display().lower()}.",
            _MINE_URL,
        )
