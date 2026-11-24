"""In-app notices for Out-on-Pass: only the next person to act, and the filer
when it is decided (CLAUDE.md 8B)."""

from django.urls import reverse

from notifications.services import notify

from .models import OutOnPass as P
from .routing import step_actors


def _when(p):
    return f"{p.pass_date:%b %d}, {p.time_out:%I:%M %p}"


def notify_next(p):
    url = reverse("outonpass:detail", args=[p.pk])
    if p.status == P.SUBMITTED and p.recorded_only:
        notify(step_actors(p), f"The Chief of Hospital filed a personal pass ({_when(p)}) for recording.", url)
    elif p.status in P.PENDING_STATUSES:
        what = p.get_kind_display().lower()
        verb = "endorsement" if p.status == P.SUBMITTED else "decision"
        notify(step_actors(p), f"{p.employee} filed a {what} ({_when(p)}) - awaiting your {verb}.", url)
    elif p.status in P.CLEARED_STATUSES:
        notify(p.employee, f"Your pass for {_when(p)} is {p.get_status_display().lower()}. "
                           "Tap Out on a hospital computer when you leave, and Tap In when you are back.", url)
    elif p.status in (P.RETURNED, P.REJECTED):
        last = p.actions.order_by("-acted_at", "-pk").first()
        remark = f' Remark: "{last.notes}"' if last and last.notes else ""
        notify(p.employee, f"Your pass for {_when(p)} was {p.get_status_display().lower()}.{remark}", url)
