"""Who may do what with a duty schedule, and the routing steps (see
models.py for the owner decisions)."""

from decimal import Decimal

from accounts.models import RoleAssignment
from leave.permissions import is_administrative_officer, is_chief_of_hospital, is_hr

from .models import DutySchedule

S = DutySchedule

# current status -> (next status, action code, button label, who acts)
STEPS = {
    S.SUBMITTED: (S.REVIEWED, "review", "Mark as reviewed", "HR"),
    S.REVIEWED: (S.RECOMMENDED, "recommend", "Recommend approval", "Administrative Officer"),
    S.RECOMMENDED: (S.APPROVED, "approve", "Approve", "Chief of Hospital"),
    S.APPROVED: (S.RECORDED, "record", "Record and publish", "HR"),
}
RETURNABLE = {S.SUBMITTED, S.REVIEWED, S.RECOMMENDED}


def _supervisor_assignments(acting):
    return acting.active_role_assignments().filter(role=RoleAssignment.SUPERVISOR) if acting else []


def supervises_area(acting, section=None, unit=None):
    """Supervisor (incl. OIC) of this section, or of this unit, or of the
    unit's section, or hospital-wide."""
    for ra in _supervisor_assignments(acting):
        if ra.section_id is None and ra.unit_id is None:
            return True
        if section is not None and ra.section_id == section.pk:
            return True
        if unit is not None and (ra.unit_id == unit.pk or ra.section_id == unit.section_id):
            return True
    return False


def preparer_role(acting, section=None, unit=None):
    """'SUPERVISOR', 'HR' or None. A Supervisor of the area prepares as
    Supervisor; HR (Processor/Administrator) may prepare any area (owner
    decision 2026-10-09)."""
    if acting is None:
        return None
    if supervises_area(acting, section, unit):
        return S.FILER_SUPERVISOR
    if is_hr(acting):
        return S.FILER_HR
    return None


def can_prepare(acting, schedule):
    return preparer_role(acting, schedule.section, schedule.unit) is not None


def can_edit(acting, user, schedule):
    return schedule.is_editable and (schedule.prepared_by_id == user.pk or can_prepare(acting, schedule))


def can_view(acting, user, schedule):
    if acting is None:
        return False
    if schedule.prepared_by_id == user.pk or is_hr(acting) or is_administrative_officer(acting) \
            or is_chief_of_hospital(acting):
        return True
    if supervises_area(acting, schedule.section, schedule.unit):
        return True
    return schedule.is_published and schedule.rows.filter(employee=acting).exists()


def may_act(acting, user, schedule):
    if acting is None or schedule.prepared_by_id == user.pk or schedule.status not in STEPS:
        return False  # the preparer never acts on their own schedule later
    who = STEPS[schedule.status][3]
    if who == "HR":
        return is_hr(acting)
    if who == "Administrative Officer":
        return is_administrative_officer(acting)
    return is_chief_of_hospital(acting)


def available_actions(acting, user, schedule):
    if not may_act(acting, user, schedule):
        return []
    _, code, label, _ = STEPS[schedule.status]
    actions = [(code, label)]
    if schedule.status in RETURNABLE:
        actions.append(("return", "Return"))
    return actions


def can_correct(acting, schedule):
    """HR corrections to a locked (approved/recorded) schedule, with a reason."""
    return schedule.is_locked and is_hr(acting)


def row_totals(cells):
    """(total paid hours, total duty days) for one row's cells."""
    hours, days = Decimal("0"), 0
    for c in cells:
        if c.shift_id:
            hours += c.shift.paid_hours
            days += 1 if c.shift.is_duty else 0
    return hours, days


def schedules_waiting_for(acting, user):
    return [s for s in S.objects.filter(status__in=list(STEPS)).select_related("section", "unit")
            .order_by("updated_at") if may_act(acting, user, s)]
