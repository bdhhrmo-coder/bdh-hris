"""
CTO claims — settled 2026-09-28 (CLAUDE.md §7): approved OT does NOT
create CTO credit by itself. CTO credit is created only when HR files a
claim from the approved OT and submits it with the required attachments.

A claim (cto.CTOCreditEntry) is a DRAFT until submitted, so documents can
be attached to it first. claim_submission_errors() is the single list of
§7 checks a claim must pass before credit_claim() touches the ledger:
  - every mandatory document requirement is met (Allowed to Work form,
    DTR/logbook copy, OT Accomplishment Report — or the Medical Transport
    set; see documents/requirements.py)
  - filed no earlier than the month after the workday
  - filed on or before that year's filing deadline (settings, Nov 30)
  - a multiplier rate is configured for the work date (CTOMultiplierRate)
  - the employee is CTO-eligible (not the Chief of Hospital)
  - for an OT claim: the OT is still approved and its approved hours
    aren't exceeded across all of its claims
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from documents.requirements import requirement_status_for

from .balances import compute_credit, filed_too_early, filing_window_opens, violates_filing_deadline
from .models import CTOCreditEntry, CTOCreditTransaction, CTOMultiplierRate
from .permissions import is_cto_eligible


def claimed_hours(ot_request, exclude_entry=None):
    """Hours already claimed (draft or credited) against one OT request."""
    qs = ot_request.cto_claims.all()
    if exclude_entry is not None and exclude_entry.pk:
        qs = qs.exclude(pk=exclude_entry.pk)
    return qs.aggregate(total=Sum("hours_worked"))["total"] or Decimal("0")


def remaining_claimable_hours(ot_request, exclude_entry=None):
    return max((ot_request.hours_requested or Decimal("0")) - claimed_hours(ot_request, exclude_entry), Decimal("0"))


def claimable_ot_requests():
    """Approved OT/rest-day/holiday requests HR can still file claims from:
    CTO-eligible employee, and approved hours not yet fully claimed."""
    from official_requests.models import OfficialRequest

    candidates = OfficialRequest.objects.filter(
        request_type=OfficialRequest.OT_RESTDAY_HOLIDAY, status=OfficialRequest.APPROVED
    ).select_related("employee").order_by("start_date")
    result = []
    for ot in candidates:
        if not is_cto_eligible(ot.employee):
            continue
        ot.remaining_hours = remaining_claimable_hours(ot)
        if ot.remaining_hours > 0:
            result.append(ot)
    return result


def claim_submission_errors(entry, today):
    """Every reason this draft claim can't be credited yet (empty = OK)."""
    errors = []
    if entry.status != CTOCreditEntry.DRAFT:
        return ["This claim has already been credited."]

    if not is_cto_eligible(entry.employee):
        errors.append("CTO is not applicable to the Chief of Hospital.")

    missing = [r["label"] for r in requirement_status_for(entry) if r["is_mandatory"] and not r["satisfied"]]
    if missing:
        errors.append("Required documents not yet uploaded: " + "; ".join(missing) + ".")

    if filed_too_early(entry.work_date, today):
        errors.append(
            f"A claim for work on {entry.work_date} can only be filed from "
            f"{filing_window_opens(entry.work_date):%B %d, %Y} (the month after the workday)."
        )
    if violates_filing_deadline(entry.work_date, today):
        deadline = date(entry.work_date.year, *settings.CTO_FILING_DEADLINE_MONTH_DAY)
        errors.append(
            f"The filing deadline for {entry.work_date.year} work ({deadline:%B %d, %Y}) has passed. "
            "A Chief of Hospital exception is required."
        )

    if CTOMultiplierRate.active_as_of(entry.work_date) is None:
        errors.append("No CTO multiplier rate is configured as of the work date.")

    ot = entry.ot_request
    if ot is not None:
        if ot.status != ot.APPROVED:
            errors.append("The linked OT request is no longer approved.")
        if entry.hours_worked > remaining_claimable_hours(ot, exclude_entry=entry):
            errors.append("This claim would exceed the OT request's approved hours.")
    return errors


@transaction.atomic
def credit_claim(entry, actor_user, today):
    """Credit a draft claim that passed claim_submission_errors(): snapshot
    the multiplier in effect on the work date, mark it CREDITED, and add
    the EARNED ledger row. Returns the list of errors (empty on success)."""
    entry = CTOCreditEntry.objects.select_for_update().get(pk=entry.pk)
    errors = claim_submission_errors(entry, today)
    if errors:
        return errors

    rate = CTOMultiplierRate.active_as_of(entry.work_date)
    multiplier, credited_hours, credited_days = compute_credit(
        entry.hours_worked, entry.is_restday_or_holiday, rate, entry.employee.shift_hours
    )
    entry.multiplier_applied = multiplier
    entry.credited_hours = credited_hours
    entry.credited_days = credited_days
    entry.shift_hours_used = entry.employee.shift_hours
    entry.status = CTOCreditEntry.CREDITED
    entry.credited_by = actor_user
    entry.credited_at = timezone.now()
    entry.save()

    CTOCreditTransaction.objects.create(
        employee=entry.employee,
        transaction_type=CTOCreditTransaction.EARNED,
        days=entry.credited_days,
        transaction_date=entry.work_date,
        credit_entry=entry,
        created_by=actor_user,
    )
    return []
