"""Mirrors leave/notifications.py's shape (see that module's docstring for
the CLAUDE.md §11/§2 in-app-only-for-now rationale), extended with the two
states leave/cto don't have: PENDING_CONSENT (notify Employee B) and
CONSENT_DECLINED (notify both parties the request is dead). Both parties'
supervisors are notified at the endorsement step since either one may
endorse (§8 — see permissions.is_supervisor_of_either_party)."""

from leave.permissions import ao_step_actors, coh_step_actors, endorsers_for, hr_step_actors, second_endorsers_for
from notifications.services import notify

from .models import DutyExchangeRequest

_QUEUE_URL = "/exchange/queue/"
_MINE_URL = "/exchange/mine/"

_OUTCOME_STATUSES = {
    DutyExchangeRequest.APPROVED,
    DutyExchangeRequest.REJECTED,
    DutyExchangeRequest.RETURNED,
    DutyExchangeRequest.CONSENT_DECLINED,
}


def notify_status_change(exchange_request):
    status = exchange_request.status
    a, b = exchange_request.employee_a, exchange_request.employee_b

    if status == DutyExchangeRequest.PENDING_CONSENT:
        notify(b, f"{a} wants to exchange duty with you on {exchange_request.date_a} — your consent is needed.", _MINE_URL)
    elif status == DutyExchangeRequest.SUBMITTED:
        notify(
            endorsers_for([a, b]),
            f"{a} and {b} filed a duty exchange request — awaiting your endorsement.",
            _QUEUE_URL,
        )
    elif status == DutyExchangeRequest.ENDORSED_BY_UNIT_HEAD:
        notify(
            second_endorsers_for([a, b]),
            f"A duty exchange request between {a} and {b} was endorsed by the unit head — awaiting your endorsement.",
            _QUEUE_URL,
        )
    elif status == DutyExchangeRequest.ENDORSED_BY_SUPERVISOR:
        notify(hr_step_actors([a, b]), f"A duty exchange request between {a} and {b} is ready for HR processing.", _QUEUE_URL)
    elif status == DutyExchangeRequest.PROCESSED_BY_HR:
        notify(
            ao_step_actors([a, b]),
            f"A duty exchange request between {a} and {b} is ready for AO recommendation.",
            _QUEUE_URL,
        )
    elif status == DutyExchangeRequest.RECOMMENDED_BY_AO:
        notify(
            coh_step_actors([a, b]),
            f"A duty exchange request between {a} and {b} is awaiting your approval.",
            _QUEUE_URL,
        )
    elif status in _OUTCOME_STATUSES:
        notify([a, b], f"Your duty exchange request was {exchange_request.get_status_display().lower()}.", _MINE_URL)
