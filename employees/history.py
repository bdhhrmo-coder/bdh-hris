"""Assignment history (owner decision N4, 2026-11-22; rebuilt 2026-11-26).

A dated record of where each employee works and under what personnel action.
  - An HR Processor or HR Administrator encodes a line (never on their own
    record; the effective date cannot be in the future). Another HR
    Administrator verifies it (never the encoder or the employee).
  - Closing a line (an end date) is requested and verified the same way.
  - At verification the employee's current sections and units are set from
    their verified open lines (sync_membership), so leave, CTO and schedule
    routing keep reading the same membership tables as before.
  - Opening lines: one verified line per current section and unit for every
    active employee who has no lines yet (load_opening_lines); this changes
    nobody's membership and needs no second verification.
  - Once an employee has lines, the employee edit form and the Data Import
    can no longer change their sections or units.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import RoleAssignment as R

from .models import AssignmentAction, AssignmentLine, PersonnelActionType

L = AssignmentLine


def is_hr(acting):
    return acting is not None and (acting.has_role(R.HR_PROCESSOR) or acting.has_role(R.HR_ADMINISTRATOR))


def is_hr_admin(acting):
    return acting is not None and acting.has_role(R.HR_ADMINISTRATOR)


def has_lines(employee):
    return L.objects.filter(employee=employee).exclude(status=L.REJECTED).exists()


def _log(line, action, user, notes=""):
    AssignmentAction.objects.create(line=line, action=action, notes=(notes or "")[:255], acted_by=user)


def _overlaps(employee, section, unit, start, end, exclude=None):
    qs = L.objects.filter(employee=employee, section=section, unit=unit).exclude(status=L.REJECTED)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    far = start.replace(year=9999)
    for other in qs:
        if start <= (other.effective_to or far) and other.effective_from <= (end or far):
            return other
    return None


def encode(acting, user, employee, section, unit, action_type, effective_from, position="", reference="",
           remarks=""):
    if not is_hr(acting):
        raise ValidationError("Only HR encodes assignment lines.")
    if acting.pk == employee.pk:
        raise ValidationError("You cannot encode a line on your own record; another HR person must.")
    if section is None:
        raise ValidationError("Choose the section.")
    if unit is not None and unit.section_id != section.pk:
        raise ValidationError(f"{unit} is not a unit of {section}.")
    if effective_from > timezone.localdate():
        raise ValidationError("The effective date cannot be in the future. Encode it on or after that date.")
    if not action_type.is_active:
        raise ValidationError("That personnel action type is switched off.")
    clash = _overlaps(employee, section, unit, effective_from, None)
    if clash:
        raise ValidationError(f"This overlaps another line for the same area (from {clash.effective_from:%b %d, %Y}). "
                              "Close that line first.")
    line = L.objects.create(employee=employee, section=section, unit=unit, action_type=action_type,
                            effective_from=effective_from, position=position or employee.position,
                            employment_status=employee.employment_status, reference=reference.strip(),
                            remarks=remarks.strip(), encoded_by=user)
    _log(line, "encode", user)
    return line


def _may_verify(acting, line, by_user):
    if not is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator verifies assignment lines.")
    if acting.pk == line.employee_id:
        raise ValidationError("You cannot verify a line on your own record.")
    if by_user is not None and by_user.pk == getattr(acting.user, "pk", None):
        raise ValidationError("Another HR Administrator must verify what you encoded.")


@transaction.atomic
def verify(acting, user, line, approve=True, notes=""):
    if line.status != L.PENDING:
        raise ValidationError("This line is not waiting for verification.")
    _may_verify(acting, line, line.encoded_by)
    if not approve and not (notes or "").strip():
        raise ValidationError("Type the reason for not verifying it.")
    line.status = L.VERIFIED if approve else L.REJECTED
    line.verified_by, line.verified_at = user, timezone.now()
    line.save(update_fields=["status", "verified_by", "verified_at"])
    _log(line, "verify" if approve else "reject", user, notes)
    if approve:
        sync_membership(line.employee)
    return line


def request_close(acting, user, line, close_on, reason):
    if not is_hr(acting):
        raise ValidationError("Only HR closes assignment lines.")
    if acting.pk == line.employee_id:
        raise ValidationError("You cannot close a line on your own record.")
    if not line.is_current:
        raise ValidationError("Only a current, verified line can be closed.")
    if line.close_on:
        raise ValidationError("A closing is already waiting for verification.")
    if close_on < line.effective_from:
        raise ValidationError("The end date cannot be before the line's start.")
    if close_on > timezone.localdate():
        raise ValidationError("The end date cannot be in the future.")
    if not (reason or "").strip():
        raise ValidationError("Type the reason (e.g. the office order).")
    line.close_on, line.close_reason, line.close_requested_by = close_on, reason.strip()[:255], user
    line.save(update_fields=["close_on", "close_reason", "close_requested_by"])
    _log(line, "close_request", user, reason)


@transaction.atomic
def verify_close(acting, user, line, approve=True, notes=""):
    if not line.close_on:
        raise ValidationError("No closing is waiting.")
    _may_verify(acting, line, line.close_requested_by)
    if not approve and not (notes or "").strip():
        raise ValidationError("Type the reason for not verifying it.")
    if approve:
        line.effective_to = line.close_on
    line.close_on, line.close_requested_by = None, None
    if not approve:
        line.close_reason = ""
    line.save(update_fields=["effective_to", "close_on", "close_requested_by", "close_reason"])
    _log(line, "close_verify" if approve else "close_reject", user, notes)
    if approve:
        sync_membership(line.employee)


def sync_membership(employee):
    """Current sections/units = the verified open lines."""
    current = L.objects.filter(employee=employee, status=L.VERIFIED, effective_to__isnull=True)
    employee.sections.set({ln.section_id for ln in current})
    employee.units.set({ln.unit_id for ln in current if ln.unit_id})


def opening_type():
    return PersonnelActionType.objects.filter(name="Opening line (go-live)").first() or \
        PersonnelActionType.objects.get_or_create(name="Opening line (go-live)")[0]


@transaction.atomic
def load_opening_lines(acting, user, as_of):
    """One verified opening line per current section and unit, for every
    active employee with no lines yet. Membership is not changed."""
    from .models import Employee

    if not is_hr_admin(acting):
        raise ValidationError("Only an HR Administrator loads opening lines.")
    kind = opening_type()
    created = 0
    for emp in Employee.objects.filter(is_active=True, archived_at__isnull=True).exclude(pk=acting.pk):
        if has_lines(emp):
            continue
        units = list(emp.units.select_related("section"))
        pairs = [(u.section, u) for u in units]
        unit_sections = {u.section_id for u in units}
        pairs += [(s, None) for s in emp.sections.all() if s.pk not in unit_sections]
        for section, unit in pairs:
            line = L.objects.create(employee=emp, section=section, unit=unit, action_type=kind,
                                    effective_from=as_of, position=emp.position,
                                    employment_status=emp.employment_status, remarks="opening line",
                                    is_opening=True, status=L.VERIFIED, encoded_by=user, verified_by=user,
                                    verified_at=timezone.now())
            _log(line, "opening", user, f"as of {as_of:%b %d, %Y}")
            created += 1
    return created


def waiting_for(acting):
    """Lines and closings an HR Administrator may verify now."""
    if not is_hr_admin(acting):
        return L.objects.none()
    qs = L.objects.filter(Q(status=L.PENDING) | Q(close_on__isnull=False)).exclude(employee=acting)
    me = acting.user
    return qs.exclude(Q(status=L.PENDING, encoded_by=me) | Q(close_on__isnull=False, close_requested_by=me)) \
        .select_related("employee", "section", "unit", "action_type", "encoded_by__employee")
