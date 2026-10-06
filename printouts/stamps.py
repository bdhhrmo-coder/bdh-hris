"""
Turns a request's action log into the list of stamps to print.

Each form describes its routing as a list of StepDef - which action code
completes that step, and what the stamp should say. compute_stamps() then
reads the request's actions (already ordered by acted_at) and returns one
Stamp per step, plus an optional "stopped" stamp when the request was
returned, rejected, declined or cancelled.

Steps completed before a return or rejection stay stamped - they did
happen, and the paper trail should show who acted before it stopped. The
red "stopped" stamp is shown only when the stopping action is the latest
entry in the log, so a request that is later picked up again (not
possible today) would not keep a stale RETURNED banner.
"""

from dataclasses import dataclass

from django.utils import timezone

STOPPING_ACTIONS = {
    "reject": "REJECTED",
    "return": "RETURNED",
    "decline": "CONSENT DECLINED",
    "cancel": "CANCELLED",
}


@dataclass(frozen=True)
class StepDef:
    role_label: str      # who acts at this step, e.g. "Supervisor"
    action_code: object  # the action that completes it, e.g. "endorse" (or a tuple of codes)
    stamp_text: str      # e.g. "DIGITALLY ENDORSED"


@dataclass
class Stamp:
    role_label: str
    state: str           # "done" | "pending" | "stopped"
    text: str            # e.g. "DIGITALLY APPROVED", "PENDING", "RETURNED"
    name: str = ""
    position: str = ""
    when: str = ""       # local date and time, e.g. "Oct 06, 2026  09:45 AM"
    notes: str = ""


def actor_name(user):
    employee = getattr(user, "employee", None)
    if employee is not None:
        return employee.full_name
    return user.get_full_name() or user.username


def actor_position(user):
    employee = getattr(user, "employee", None)
    return (employee.position or "") if employee is not None else ""


def format_when(dt):
    return timezone.localtime(dt).strftime("%b %d, %Y  %I:%M %p")


def compute_stamps(actions, step_defs):
    """Return (stamps, stopped_stamp_or_None). `actions` is an iterable of
    action-log rows with .action, .acted_by, .acted_at and .notes."""
    actions = list(actions)
    latest = {}
    for a in actions:
        latest[a.action] = a
    stopped_by = actions[-1] if actions and actions[-1].action in STOPPING_ACTIONS else None

    stamps = []
    for step in step_defs:
        codes = step.action_code if isinstance(step.action_code, tuple) else (step.action_code,)
        done = [latest[c] for c in codes if c in latest]
        a = max(done, key=lambda x: x.acted_at) if done else None
        if a is not None:
            stamps.append(Stamp(
                role_label=step.role_label, state="done", text=step.stamp_text,
                name=actor_name(a.acted_by), position=actor_position(a.acted_by), when=format_when(a.acted_at),
            ))
        else:
            stamps.append(Stamp(role_label=step.role_label, state="pending", text="PENDING"))

    stopped = None
    if stopped_by is not None:
        stopped = Stamp(
            role_label="", state="stopped", text=STOPPING_ACTIONS[stopped_by.action],
            name=actor_name(stopped_by.acted_by), position=actor_position(stopped_by.acted_by),
            when=format_when(stopped_by.acted_at), notes=stopped_by.notes or "",
        )
    return stamps, stopped
