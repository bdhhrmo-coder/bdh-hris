"""
Filing an OT batch (CLAUDE.md §7.1). Filed only by a Supervisor (Unit Head
in Nursing), HR Processor/Administrator or AO — never by the employee.
One line per employee per date, with approved hours and day type. The
filer cannot add themselves; nobody can add the COH. Past dates need a
typed reason. Approving OT never creates CTO credit.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import RoleAssignment
from leave.permissions import supervisors_of
from notifications.services import notify

from . import routing
from .models import OvertimeLine, OvertimeRequest, RoutedRecord

CAPACITY_ROLES = {
    OvertimeRequest.CAPACITY_SUPERVISOR: {RoleAssignment.SUPERVISOR},
    OvertimeRequest.CAPACITY_HR: {RoleAssignment.HR_PROCESSOR, RoleAssignment.HR_ADMINISTRATOR},
    OvertimeRequest.CAPACITY_AO: {RoleAssignment.ADMINISTRATIVE_OFFICER},
}


def filing_capacities(employee):
    """The capacities this employee may file OT in."""
    if employee is None:
        return []
    return [cap for cap, roles in CAPACITY_ROLES.items() if any(employee.has_role(r) for r in roles)]


def line_errors(filer, lines, ot=None):
    """lines: [{"employee", "work_date", "approved_hours", "day_type"}].
    ot: the returned request being edited, if any (its own lines don't
    count as duplicates)."""
    errors = []
    if not lines:
        errors.append("Add at least one line.")
    seen = set()
    for line in lines:
        employee, work_date = line["employee"], line["work_date"]
        label = f"{employee} on {work_date}"
        if employee.pk == filer.pk:
            errors.append("You cannot add yourself to your own OT request.")
        if employee.has_role(RoleAssignment.CHIEF_OF_HOSPITAL):
            errors.append(f"{employee}: the Chief of Hospital cannot be on an OT line (CTO not applicable).")
        if employee.separation_status and employee.last_day_of_service and work_date > employee.last_day_of_service:
            errors.append(f"{label}: after their last day of service ({employee.last_day_of_service}).")
        if (employee.pk, work_date) in seen:
            errors.append(f"{label} appears twice.")
        seen.add((employee.pk, work_date))
        elsewhere = OvertimeLine.objects.filter(
            employee=employee, work_date=work_date,
            request__status__in=[RoutedRecord.PENDING, RoutedRecord.APPROVED],
        )
        if ot is not None:
            elsewhere = elsewhere.exclude(request=ot)
        other = elsewhere.select_related("request").first()
        if other:
            errors.append(f"{label} is already on OT request #{other.request_id} ({other.request.get_status_display().lower()}).")
        hours = line["approved_hours"]
        if hours is None or hours <= 0 or hours > Decimal("24"):
            errors.append(f"{label}: approved hours must be more than 0 and at most 24.")
    return errors


@transaction.atomic
def file_ot(filer, filed_as, purpose, past_date_reason, lines, user, today, ot=None):
    """Create (or, for a returned batch, replace the lines of) an OT
    request and submit it. Returns (ot, warnings)."""
    if filed_as not in filing_capacities(filer):
        raise ValidationError("You do not hold the role you are filing as.")
    errors = line_errors(filer, lines, ot)
    if any(line["work_date"] < today for line in lines) and not past_date_reason.strip():
        errors.append("A reason is required when any line is dated before today.")
    if errors:
        raise ValidationError(errors)

    if ot is None:
        ot = OvertimeRequest(filed_by=filer)
    elif ot.status != RoutedRecord.RETURNED or ot.filed_by_id != filer.pk:
        raise ValidationError("Only the filer can edit a returned OT request.")
    ot.filed_as = filed_as
    ot.purpose = purpose
    ot.past_date_reason = past_date_reason.strip()
    ot.save()
    ot.lines.all().delete()
    OvertimeLine.objects.bulk_create(OvertimeLine(request=ot, **line) for line in lines)

    route = routing.ot_route(filer, filed_as)
    on_route = routing.people_on_route(ot, route)
    warnings = [
        f"{person} is on this request and also approves it; consider filing their line as a separate batch."
        for person in on_route if person.pk != filer.pk
    ]
    routing.submit(ot, route, user)

    for employee in {line["employee"] for line in lines}:
        others = [s for s in supervisors_of(employee) if s.pk != filer.pk]
        notify(others, f"{employee} was added to an OT request filed by {filer} (for information).",
               ot.get_absolute_url())
    return ot, warnings
