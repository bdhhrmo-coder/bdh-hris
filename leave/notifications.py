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
from .permissions import (
    ao_step_actors, coh_filed_regular, coh_step_actors, endorsers_for, hr_step_actors, remark_suffix,
    second_endorsers_for,
)

_QUEUE_URL = "/leave/queue/"
_MINE_URL = "/leave/mine/"

_OUTCOME_STATUSES = {
    LeaveApplication.APPROVED,
    LeaveApplication.RECORDED,
    LeaveApplication.REJECTED,
    LeaveApplication.RETURNED,
}


def _last_actor(application, action):
    a = application.actions.filter(action=action).select_related("acted_by__employee").order_by("-pk").first()
    return getattr(a.acted_by, "employee", None) if a else None


def notify_status_change(application):
    A = LeaveApplication
    status = application.status
    filers = [application.employee]
    name = application.leave_type.name
    who = application.employee
    regular = application.route == A.ROUTE_REGULAR

    if status == A.SUBMITTED:
        if coh_filed_regular(application):
            notify(hr_step_actors(filers, application),
                   f"{who} filed a {name} application - ready for HR recording and verification.", _QUEUE_URL)
        else:
            notify(endorsers_for(filers), f"{who} filed a {name} application - awaiting your endorsement.",
                   _QUEUE_URL)
    elif status == A.ENDORSED_BY_UNIT_HEAD:
        notify(second_endorsers_for(filers),
               f"A {name} application for {who} was endorsed by the unit head - awaiting your endorsement.",
               _QUEUE_URL)
    elif status == A.ENDORSED_BY_SUPERVISOR:
        step = "recording and verification" if regular else "processing"
        notify(hr_step_actors(filers, application), f"A {name} application for {who} is ready for HR {step}.",
               _QUEUE_URL)
    elif status == A.PROCESSED_BY_HR:
        step = "review" if regular else "recommendation"
        notify(ao_step_actors(filers), f"A {name} application for {who} is ready for AO {step}.", _QUEUE_URL)
    elif status == A.RECOMMENDED_BY_AO:
        step = "recommendation for approval (Capitol)" if regular else "approval"
        notify(coh_step_actors(filers), f"A {name} application for {who} is awaiting your {step}.", _QUEUE_URL)
    elif status == A.FOR_CAPITOL_APPROVAL:
        notify(who, f"Your {name} application was recommended for approval and is now for Capitol approval.",
               _MINE_URL)
        # Only the HR person who recorded it, to forward it to the Capitol.
        recorder = _last_actor(application, "record")
        if recorder is not None:
            notify(recorder, f"The {name} application of {who} is for Capitol approval - please forward it.",
                   f"/leave/{application.pk}/")
    elif status in _OUTCOME_STATUSES:
        last = application.actions.order_by("-pk").first()
        what = application.status_label.lower()
        if last and last.action in ("capitol_return", "capitol_reject"):
            what = ("returned" if last.action == "capitol_return" else "rejected") + " by the Capitol"
        if status in (A.RETURNED, A.REJECTED) and regular and application.ledger_entries.exists():
            what += " (the leave days were given back)"
        notify(who, f"Your {name} application was {what}.{remark_suffix(application)}", _MINE_URL)
