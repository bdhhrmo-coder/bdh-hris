"""
CTO claims (CLAUDE.md §7.1, pending approved HR memo). The employee files a
claim with one or more lines; it is routed as one (cto/routing.py) and
credited per line only on COH approval. Approving OT never credits CTO.

Checks when a line is added (judged on today):
  - the claim is a draft or returned, and the employee is CTO-eligible
  - OT line: an approved one-day line of main's OT module (official_requests,
    owner decision D4), the claimant's own, not recorded-only (the COH's own
    OT never creates CTO, D2), not already on another live claim; actual
    hours <= approved hours. Day type comes from the batch (D4/8d; 8h for a
    line filed without a batch).
  - the workday is not in the future
  - the line is not past its filing deadline (exception claims skip this)
  - no more than 24 hours per employee per date across all live claims
  - a multiplier rate exists for the workday
Checks at submission: every mandatory document is uploaded, the claim has
lines, and every line is within its filing deadline as of the FIRST filing
date (so a returned claim does not expire while being corrected).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from documents.requirements import requirement_status_for
from notifications.services import notify

from . import routing
from .carryover import initial_use_deadline
from .balances import compute_credit, effective_date_for, filing_deadline_for, violates_filing_deadline
from official_requests.models import OfficialRequest, OfficialRequestBatch

from .models import HOLIDAY, REST_DAY, WEEKDAY, CTOClaim, CTOClaimLine, CTOMultiplierRate, RoutedRecord
from .permissions import is_cto_eligible

MAX_HOURS_PER_DATE = Decimal("24")


def _live_lines():
    return CTOClaimLine.objects.exclude(claim__status=RoutedRecord.CANCELLED)


WORK_KIND_DAY_TYPE = {
    OfficialRequestBatch.WORK_OT: WEEKDAY,
    OfficialRequestBatch.WORK_REST_DAY: REST_DAY,
    OfficialRequestBatch.WORK_HOLIDAY: HOLIDAY,
}


def ot_day_type(ot_line):
    """Day type of an approved OT line: the batch's kind (one per batch, 8d).
    A line filed without a batch has only the yes/no rest-day-or-holiday
    flag: yes -> rest day, no -> weekday (owner decision 8h). HR may correct
    it on the claim with a remark."""
    if ot_line.batch_id and ot_line.batch.work_kind:
        return WORK_KIND_DAY_TYPE[ot_line.batch.work_kind]
    return REST_DAY if ot_line.is_restday_or_holiday else WEEKDAY


def claimable_ot_lines(employee):
    """Approved one-day OT lines of this employee not on a live claim yet.
    RECORDED (the COH's own OT, approved outside BDH) never counts, and a
    multi-day request filed without a batch cannot be split into lines."""
    taken = _live_lines().filter(ot_line__isnull=False).values_list("ot_line_id", flat=True)
    return (
        OfficialRequest.objects.filter(
            employee=employee, request_type=OfficialRequest.OT_RESTDAY_HOLIDAY, status=OfficialRequest.APPROVED,
            recorded_only=False, start_date=F("end_date"), hours_requested__isnull=False,
        )
        .exclude(pk__in=taken)
        .select_related("batch")
        .order_by("start_date", "pk")
    )


def hours_on_date(employee, work_date, exclude_line=None):
    qs = _live_lines().filter(claim__employee=employee, work_date=work_date)
    if exclude_line is not None:
        qs = qs.exclude(pk=exclude_line.pk)
    return qs.aggregate(total=Sum("hours_worked"))["total"] or Decimal("0")


def _preview(line, employee):
    rate = CTOMultiplierRate.active_as_of(line.work_date)
    if rate is None:
        raise ValidationError(f"No CTO multiplier rate is configured as of {line.work_date}.")
    line.shift_hours_used = employee.shift_hours
    line.multiplier_applied, line.credited_hours, line.credited_days = compute_credit(
        line.hours_worked, line.day_type != WEEKDAY, rate, employee.shift_hours
    )


@transaction.atomic
def add_line(claim, hours_worked, today, ot_line=None, work_date=None, day_type=None):
    """Validate and add one line; raises ValidationError with the reason."""
    employee = claim.employee
    # Lock the employee row: two lines added at once can't both pass the
    # 24-hour cap or both take the same OT line.
    type(employee).objects.select_for_update().get(pk=employee.pk)
    if claim.status not in RoutedRecord.EDITABLE_STATUSES:
        raise ValidationError("Lines can only be changed while the claim is a draft or returned.")
    if not is_cto_eligible(employee):
        raise ValidationError("CTO is not applicable to the Chief of Hospital.")
    if hours_worked is None or hours_worked <= 0:
        raise ValidationError("Hours worked must be greater than zero.")

    if claim.uses_ot_lines():
        if ot_line is None:
            raise ValidationError("Pick an approved OT line.")
        if not claimable_ot_lines(employee).filter(pk=ot_line.pk).exists():
            raise ValidationError("That OT line is not approved for you or is already on another claim.")
        if hours_worked > ot_line.hours_requested:
            raise ValidationError(f"Actual hours cannot exceed the {ot_line.hours_requested} approved hours.")
        work_date, day_type = ot_line.start_date, ot_day_type(ot_line)
    else:
        if ot_line is not None:
            raise ValidationError("Medical Transport and exception claims do not use OT lines.")
        if work_date is None or day_type is None:
            raise ValidationError("Enter the workday and day type.")

    if work_date > today:
        raise ValidationError("A claim can only be filed after the workday.")

    # Exception claims skip the filing deadline (decided 2026-11-01): the HR
    # Administrator's typed reason is the justification.
    if not claim.is_exception() and violates_filing_deadline(work_date, today):
        raise ValidationError(
            f"The filing deadline for work on {work_date} was {filing_deadline_for(work_date):%B %d, %Y}."
        )
    if hours_on_date(employee, work_date) + hours_worked > MAX_HOURS_PER_DATE:
        raise ValidationError(f"More than 24 hours would be claimed for {work_date} (OT and Medical Transport combined).")

    line = CTOClaimLine(claim=claim, ot_line=ot_line, work_date=work_date, hours_worked=hours_worked, day_type=day_type)
    _preview(line, employee)
    line.save()
    return line


def submission_errors(claim, today):
    errors = []
    if claim.status not in RoutedRecord.EDITABLE_STATUSES:
        return ["This claim has already been submitted."]
    if not is_cto_eligible(claim.employee):
        errors.append("CTO is not applicable to the Chief of Hospital.")
    lines = list(claim.lines.all())
    if not lines:
        errors.append("Add at least one line.")
    filed_on = claim.first_filed_on or today
    deadline_lines = [] if claim.is_exception() else lines  # exception claims skip the deadline
    for line in deadline_lines:
        if violates_filing_deadline(line.work_date, filed_on):
            errors.append(
                f"Work on {line.work_date}: filing deadline was {filing_deadline_for(line.work_date):%B %d, %Y}."
            )
    missing = [r["label"] for r in requirement_status_for(claim) if r["is_mandatory"] and not r["satisfied"]]
    if missing:
        errors.append("Required documents not yet uploaded: " + "; ".join(missing) + ".")
    return errors


@transaction.atomic
def credit_lines(claim):
    """On COH approval: recompute each line with the rate in force on its
    workday, and set its effective date and year earned."""
    for line in claim.lines.select_for_update():
        _preview(line, claim.employee)
        line.effective_date = effective_date_for(line.work_date, claim.approved_on)
        line.year_earned = line.effective_date.year
        line.use_deadline = initial_use_deadline(line.effective_date, line.year_earned)
        line.state = CTOClaimLine.CREDITED
        line.save()
    total = sum((line.credited_days for line in claim.lines.all()), Decimal("0"))
    notify(claim.employee, f"{claim} was approved: {total} CTO day(s), usable from each line's effective date.",
           claim.get_absolute_url())


@transaction.atomic
def submit_claim(claim, user, today):
    errors = submission_errors(claim, today)
    if errors:
        raise ValidationError(errors)
    if claim.first_filed_on is None:
        claim.first_filed_on = today
        claim.save(update_fields=["first_filed_on"])
    routing.submit(claim, routing.claim_route(claim), user, on_approved=credit_lines)


def approve_claim(claim, employee, user, notes=""):
    """One approval step on a claim. On the final (COH) step the lines are
    credited in the same transaction; if crediting fails, nothing is
    approved or credited (see routing.approve_step)."""
    routing.approve_step(claim, employee, user, notes, on_approved=credit_lines)


@transaction.atomic
def correct_day_type(line, new_day_type, remark, employee, user):
    """HR (Processor or Administrator) at the HR step only. Changes the
    claim line, never the approved OT line; recalculates the credit."""
    claim = line.claim
    if claim.current_step != routing.STEP_HR or not routing.can_act(employee, claim):
        raise ValidationError("Only HR, at the HR review step, can correct the day type.")
    if not remark.strip():
        raise ValidationError("A remark is required to correct the day type.")
    old = line.get_day_type_display()
    line.day_type = new_day_type
    _preview(line, claim.employee)
    line.save()
    claim.actions.create(
        action="correct_day_type", step=routing.STEP_HR, acted_by=user,
        notes=f"Line {line.work_date}: {old} -> {line.get_day_type_display()}. {remark.strip()}",
    )


def create_exception_claim(hr_admin, employee, duty_type, reason, user):
    """HR Administrator only, never for themselves; a reason is required.
    Routed AO -> COH when submitted."""
    if not hr_admin.is_hr_administrator():
        raise ValidationError("Only an HR Administrator may file an exception CTO claim.")
    if employee.pk == hr_admin.pk:
        raise ValidationError("You cannot file an exception claim for yourself.")
    if not is_cto_eligible(employee):
        raise ValidationError("CTO is not applicable to the Chief of Hospital.")
    if not reason.strip():
        raise ValidationError("A reason is required for an exception claim.")
    return CTOClaim.objects.create(employee=employee, duty_type=duty_type, filed_by=user, exception_reason=reason.strip())
