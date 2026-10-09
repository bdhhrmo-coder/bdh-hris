"""Changes to duty schedules. Every routing step goes to ScheduleAction and
every change after COH approval to ScheduleChange (both in the Audit Log)."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import RoleAssignment
from leave.permissions import employees_with_role, hr_employees
from notifications.services import notify

from . import rules
from .models import DutySchedule, ScheduleAction, ScheduleCell, ScheduleChange, ScheduleRow, ShiftCode

S = DutySchedule


def _url(schedule):
    return f"/schedules/{schedule.pk}/"


def log(schedule, action, user, notes=""):
    ScheduleAction.objects.create(schedule=schedule, action=action, resulting_status=schedule.status,
                                  notes=notes[:255], acted_by=user)


# -- create / edit -------------------------------------------------------------------

@transaction.atomic
def create_schedule(user, role, year, month, section=None, unit=None):
    schedule = S(section=section, unit=unit, year=year, month=month, prepared_by=user, preparer_role=role,
                 cutoff_date=S.default_cutoff(year, month))
    schedule.full_clean()
    schedule.save()
    for i, emp in enumerate(schedule.area_employees().order_by("surname", "first_name")):
        add_row(schedule, emp, sort_order=i)
    log(schedule, "create", user)
    return schedule


def add_row(schedule, employee, sort_order=None):
    if sort_order is None:
        sort_order = schedule.rows.count()
    row, created = ScheduleRow.objects.get_or_create(
        schedule=schedule, employee=employee,
        defaults={"designation": employee.position or "", "sort_order": sort_order})
    if created:
        ScheduleCell.objects.bulk_create([ScheduleCell(row=row, date=d) for d in schedule.days])
    return row


@transaction.atomic
def save_grid(schedule, values):
    """values: {(row_id, date): shift_id or None}. Only while editable."""
    if not schedule.is_editable:
        raise ValidationError("This schedule can no longer be edited.")
    codes = {c.pk: c for c in ShiftCode.objects.all()}
    cells = {(c.row_id, c.date): c for c in ScheduleCell.objects.filter(row__schedule=schedule)}
    changed = []
    for key, shift_id in values.items():
        cell = cells.get(key)
        if cell is None:
            continue
        new = codes.get(shift_id) if shift_id else None
        if (cell.shift_id or None) != (new.pk if new else None):
            cell.shift = new
            changed.append(cell)
    ScheduleCell.objects.bulk_update(changed, ["shift"])
    schedule.save(update_fields=["updated_at"])
    return len(changed)


# -- routing ---------------------------------------------------------------------------

@transaction.atomic
def submit(schedule, user):
    if not schedule.is_editable:
        raise ValidationError("Only a draft or returned schedule can be submitted.")
    resubmit = schedule.status == S.RETURNED
    schedule.status = S.SUBMITTED
    schedule.save(update_fields=["status", "updated_at"])
    log(schedule, "resubmit" if resubmit else "submit", user)
    _notify_next(schedule)


@transaction.atomic
def act(schedule, user, action, notes=""):
    if action == "return":
        if not notes.strip():
            raise ValidationError("Type the reason for returning it, so the preparer knows what to fix.")
        schedule.status = S.RETURNED
        schedule.save(update_fields=["status", "updated_at"])
        log(schedule, "return", user, notes)
        notify(getattr(schedule.prepared_by, "employee", None),
               f"Your duty schedule for {schedule} was returned: \"{notes.strip()}\". Please edit and resubmit.",
               _url(schedule))
        return
    nxt, code, _, _ = rules.STEPS[schedule.status]
    if code != action:
        raise ValidationError("That step is not the next one for this schedule.")
    schedule.status = nxt
    fields = ["status", "updated_at"]
    if nxt == S.APPROVED:
        schedule.approved_at, schedule.approved_by = timezone.now(), user
        fields += ["approved_at", "approved_by"]
    if nxt == S.RECORDED:
        schedule.recorded_at = timezone.now()
        fields += ["recorded_at"]
    schedule.save(update_fields=fields)
    log(schedule, action, user, notes)
    if nxt == S.RECORDED:
        notify([r.employee for r in schedule.rows.select_related("employee")],
               f"Your duty schedule for {schedule.month_label} ({schedule.area_name}) is out.", "/schedules/mine/")
        notify(getattr(schedule.prepared_by, "employee", None),
               f"The duty schedule for {schedule} was approved and recorded.", _url(schedule))
    else:
        _notify_next(schedule)


def _notify_next(schedule):
    who = rules.STEPS.get(schedule.status, (None, None, None, None))[3]
    people = {"HR": lambda: hr_employees(),
              "Administrative Officer": lambda: employees_with_role(RoleAssignment.ADMINISTRATIVE_OFFICER),
              "Chief of Hospital": lambda: employees_with_role(RoleAssignment.CHIEF_OF_HOSPITAL)}.get(who)
    if people is None:
        return
    preparer = getattr(schedule.prepared_by, "employee", None)
    notify([p for p in people() if preparer is None or p.pk != preparer.pk],
           f"Duty schedule for {schedule} is waiting for your action.", _url(schedule))


# -- after approval ------------------------------------------------------------------------

def change_cell(schedule, employee, day, new_shift, reason, user, exchange=None):
    row = schedule.rows.filter(employee=employee).first() or add_row(schedule, employee)
    cell = row.cells.get(date=day)
    old = cell.shift.code if cell.shift_id else ""
    cell.shift = new_shift
    cell.save(update_fields=["shift"])
    ScheduleChange.objects.create(schedule=schedule, employee=employee, date=day, old_shift=old,
                                  new_shift=new_shift.code if new_shift else "", reason=reason[:255],
                                  exchange=exchange, changed_by=user)


@transaction.atomic
def hr_correction(schedule, employee, day, new_shift, reason, user):
    if not schedule.is_locked:
        raise ValidationError("Corrections are only for approved schedules; edit the draft instead.")
    if not reason.strip():
        raise ValidationError("A reason is required for a correction.")
    change_cell(schedule, employee, day, new_shift, f"HR correction: {reason.strip()}", user)
    notify(employee, f"Your duty on {day:%b %d, %Y} was changed to {new_shift.code if new_shift else 'blank'} "
                     f"({reason.strip()}).", "/schedules/mine/")


# -- checks used by the grid and by Exchange of Duty -------------------------------------

CHECKABLE = [S.SUBMITTED, S.REVIEWED, S.RECOMMENDED, S.APPROVED, S.RECORDED]


def double_bookings(schedule):
    """[(employee, date, other schedule)] where someone has a duty here AND
    a duty in another schedule on the same day."""
    out = []
    mine = ScheduleCell.objects.filter(row__schedule=schedule, shift__is_duty=True).select_related("row__employee")
    if not mine:
        return out
    by_key = {(c.row.employee_id, c.date): c for c in mine}
    others = (ScheduleCell.objects.filter(shift__is_duty=True, row__employee_id__in={k[0] for k in by_key},
                                          date__in={k[1] for k in by_key})
              .exclude(row__schedule=schedule).select_related("row__employee", "row__schedule__section",
                                                              "row__schedule__unit"))
    for c in others:
        if (c.row.employee_id, c.date) in by_key:
            out.append((c.row.employee, c.date, c.row.schedule))
    return sorted(out, key=lambda t: (t[1], t[0].surname))


def cell_for(employee, day):
    """(schedule, cell) for this employee and day, from the most advanced
    non-draft schedule; (None, None) if none is on file."""
    cells = (ScheduleCell.objects.filter(row__employee=employee, date=day, row__schedule__status__in=CHECKABLE)
             .select_related("shift", "row__schedule"))
    best = None
    for c in cells:
        rank = CHECKABLE.index(c.row.schedule.status)
        if best is None or rank > best[0]:
            best = (rank, c)
    return (best[1].row.schedule, best[1]) if best else (None, None)


def schedules_for(employee, day):
    """All non-draft schedules covering this day that list this employee."""
    return {c.row.schedule for c in ScheduleCell.objects.filter(
        row__employee=employee, date=day, row__schedule__status__in=CHECKABLE).select_related("row__schedule")}


def off_code():
    return ShiftCode.objects.filter(code="OFF").first()
