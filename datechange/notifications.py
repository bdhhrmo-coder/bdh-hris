"""In-app notices for date change requests: only the next person to act
(dry-run item 4), and the employee on the outcome. Every notice opens the
date change request itself."""

from leave.permissions import (
    ao_step_actors,
    coh_step_actors,
    endorsers_for,
    hr_step_actors,
    remark_suffix,
    second_endorsers_for,
)
from notifications.services import notify

from .models import DateChangeRequest as DC
from .rules import _hr_only


def notify_change(change):
    filers = [change.employee]
    who, what = change.employee, change.what
    url = f"/date-changes/{change.pk}/"
    head = f"{who} asked to move {what} from {change.old_dates_label} to {change.new_dates_label}"
    s = change.status
    if s == DC.SUBMITTED:
        if _hr_only(change):
            notify(hr_step_actors(filers, change), f"{head} - ready for HR.", url)
        else:
            notify(endorsers_for(filers), f"{head} - awaiting your endorsement.", url)
    elif s == DC.ENDORSED_BY_UNIT_HEAD:
        notify(second_endorsers_for(filers), f"{head} - endorsed by the unit head, awaiting your endorsement.", url)
    elif s == DC.ENDORSED_BY_SUPERVISOR:
        notify(hr_step_actors(filers, change), f"{head} - ready for HR.", url)
    elif s == DC.PROCESSED_BY_HR:
        notify(ao_step_actors(filers), f"{head} - ready for AO recommendation.", url)
    elif s == DC.RECOMMENDED_BY_AO:
        step = "recommendation" if change.regular_leave else "approval"
        notify(coh_step_actors(filers), f"{head} - awaiting your {step}.", url)
    elif s == DC.APPLIED:
        msg = f"Your {what} now runs {change.new_dates_label} (moved from {change.old_dates_label})."
        if change.regular_leave:
            msg += " Print the updated CSC Form No. 6 and send it to the Provincial Capitol as usual."
        notify(who, msg, url)
    elif s in (DC.RETURNED, DC.REJECTED):
        notify(who, f"Your date change for {what} was {change.get_status_display().lower()}. "
                    f"Your approved dates ({change.old_dates_label}) stay.{remark_suffix(change)}", url)
