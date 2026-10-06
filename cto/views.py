from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from documents.models import UploadedDocument
from documents.requirements import requirement_status_for
from official_requests.models import OfficialRequest

from printouts.http import pdf_response

from .balances import (
    compute_available_cto_balance,
    exceeds_monthly_cap,
    filing_window_opens,
    forms_illegal_consecutive_run,
    violates_filing_deadline,
    violates_usage_cutoff,
)
from .claims import claim_submission_errors, claimable_ot_requests, credit_claim, remaining_claimable_hours
from .forms import CTOClaimForm, CTOExceptionClaimForm, CTOUsageApplicationForm
from .models import CTOCreditEntry, CTOCreditTransaction, CTOUsageApplication, CTOUsageApplicationAction
from .notifications import notify_credit_entry, notify_status_change
from .permissions import (
    can_view_cto_application,
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_cto_eligible,
    is_hr,
    is_supervisor_of,
    visible_cto_applications_for,
)


def _save_draft_claim(form, user):
    entry = form.save(commit=False)
    entry.status = CTOCreditEntry.DRAFT
    entry.multiplier_applied = form.cleaned_data["_multiplier_applied"]
    entry.credited_hours = form.cleaned_data["_credited_hours"]
    entry.credited_days = form.cleaned_data["_credited_days"]
    entry.shift_hours_used = form.cleaned_data["_shift_hours_used"]
    entry.recorded_by = user
    entry.full_clean()
    entry.save()
    return entry


def _deadline_error(form, work_date):
    """A draft that could never be submitted isn't worth creating."""
    if work_date and violates_filing_deadline(work_date, date.today()):
        deadline = date(work_date.year, *settings.CTO_FILING_DEADLINE_MONTH_DAY)
        form.add_error(
            "work_date",
            f"The filing deadline for {work_date.year} work ({deadline:%B %d, %Y}) has passed. "
            "A Chief of Hospital exception is required.",
        )
        return True
    return False


@login_required
def claims_home(request):
    """HR's CTO claims screen: approved OT requests still open for claims,
    draft claims awaiting documents/submission, and recent credited ones."""
    acting_employee = get_acting_employee(request.user)
    if not is_hr(acting_employee):
        raise PermissionDenied("Only HR may file CTO claims.")
    return render(request, "cto/claims_home.html", {
        "ot_requests": claimable_ot_requests(),
        "drafts": CTOCreditEntry.objects.filter(status=CTOCreditEntry.DRAFT).select_related("employee", "ot_request"),
        "recent_credited": CTOCreditEntry.objects.filter(status=CTOCreditEntry.CREDITED)
        .select_related("employee", "ot_request").order_by("-credited_at")[:20],
        "can_file_exception": acting_employee.is_hr_administrator(),
    })


@login_required
def claim_create(request, ot_pk):
    """File a draft CTO claim for one workday of an approved OT request."""
    acting_employee = get_acting_employee(request.user)
    if not is_hr(acting_employee):
        raise PermissionDenied("Only HR may file CTO claims.")
    ot_request = get_object_or_404(
        OfficialRequest, pk=ot_pk, request_type=OfficialRequest.OT_RESTDAY_HOLIDAY, status=OfficialRequest.APPROVED
    )
    if not is_cto_eligible(ot_request.employee):
        raise PermissionDenied("CTO is not applicable to the Chief of Hospital.")

    if request.method == "POST":
        form = CTOClaimForm(request.POST, ot_request=ot_request)
        if form.is_valid() and not _deadline_error(form, form.cleaned_data["work_date"]):
            entry = _save_draft_claim(form, request.user)
            messages.success(request, "Draft claim saved. Upload the required documents, then submit it.")
            return redirect("cto:claim_detail", pk=entry.pk)
    else:
        form = CTOClaimForm(ot_request=ot_request)
    return render(request, "cto/claim_form.html", {
        "form": form, "ot_request": ot_request, "remaining_hours": remaining_claimable_hours(ot_request),
    })


@login_required
def credit_entry_create(request):
    """HR Administrator only: an exception claim with no OT request behind
    it. Saved as a draft; credited only through claim_submit like any
    other claim."""
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None or not acting_employee.is_hr_administrator():
        raise PermissionDenied("Only an HR Administrator may file an exception CTO claim.")

    if request.method == "POST":
        form = CTOExceptionClaimForm(request.POST)
        if form.is_valid() and not _deadline_error(form, form.cleaned_data["work_date"]):
            entry = _save_draft_claim(form, request.user)
            messages.success(request, "Draft exception claim saved. Upload the required documents, then submit it.")
            return redirect("cto:claim_detail", pk=entry.pk)
    else:
        form = CTOExceptionClaimForm()
    return render(request, "cto/credit_entry_form.html", {"form": form})


@login_required
def claim_detail(request, pk):
    acting_employee = get_acting_employee(request.user)
    entry = get_object_or_404(CTOCreditEntry.objects.select_related("employee", "ot_request"), pk=pk)
    hr = is_hr(acting_employee)
    if not hr and not (acting_employee is not None and acting_employee.pk == entry.employee_id):
        raise PermissionDenied("You are not authorized to view this CTO claim.")
    is_draft = entry.status == CTOCreditEntry.DRAFT
    return render(request, "cto/claim_detail.html", {
        "entry": entry,
        "requirement_status": requirement_status_for(entry),
        "submission_errors": claim_submission_errors(entry, date.today()) if is_draft else [],
        "filing_window_opens": filing_window_opens(entry.work_date),
        "filing_deadline": date(entry.work_date.year, *settings.CTO_FILING_DEADLINE_MONTH_DAY),
        "is_hr": hr,
        "can_act": hr and is_draft,
        "can_discard": hr and is_draft and not _has_documents(entry),
    })


def _has_documents(entry):
    return UploadedDocument.objects.filter(
        content_type=ContentType.objects.get_for_model(entry), object_id=entry.pk, is_active=True
    ).exists()


@login_required
def claim_submit(request, pk):
    """The only place a CTO claim credits the ledger — and only if every
    §7 check in cto/claims.py passes."""
    if request.method != "POST":
        return redirect("cto:claim_detail", pk=pk)
    acting_employee = get_acting_employee(request.user)
    if not is_hr(acting_employee):
        raise PermissionDenied("Only HR may submit CTO claims.")
    entry = get_object_or_404(CTOCreditEntry, pk=pk)

    errors = credit_claim(entry, request.user, date.today())
    if errors:
        for error in errors:
            messages.error(request, error)
    else:
        entry.refresh_from_db()
        notify_credit_entry(entry)
        messages.success(request, f"Credited {entry.credited_days} CTO day(s) to {entry.employee}.")
    return redirect("cto:claim_detail", pk=pk)


@login_required
def claim_discard(request, pk):
    """Discard a mistaken draft. Only while no documents are attached, so
    the upload audit trail never points at a deleted claim."""
    if request.method != "POST":
        return redirect("cto:claim_detail", pk=pk)
    acting_employee = get_acting_employee(request.user)
    if not is_hr(acting_employee):
        raise PermissionDenied("Only HR may discard CTO claims.")
    entry = get_object_or_404(CTOCreditEntry, pk=pk, status=CTOCreditEntry.DRAFT)
    if _has_documents(entry):
        messages.error(request, "This draft has documents attached and can't be discarded.")
        return redirect("cto:claim_detail", pk=pk)
    entry.delete()
    messages.success(request, "Draft claim discarded.")
    return redirect("cto:claims_home")


@login_required
def my_cto(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    balance = compute_available_cto_balance(acting_employee)
    applications = acting_employee.cto_usage_applications.all()
    credit_entries = acting_employee.cto_credit_entries.all()
    return render(
        request, "cto/my_cto.html",
        {"balance": balance, "applications": applications, "credit_entries": credit_entries},
    )


@login_required
def cto_apply(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    if not is_cto_eligible(acting_employee):
        raise PermissionDenied("CTO is not applicable to the Chief of Hospital.")

    if request.method == "POST":
        form = CTOUsageApplicationForm(request.POST, employee=acting_employee)
        if form.is_valid():
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            days = form.cleaned_data["number_of_days"]

            if violates_usage_cutoff(end):
                cutoff = date(end.year, *settings.CTO_USAGE_CUTOFF_MONTH_DAY)
                form.add_error("end_date", f"CTO usage cannot extend past {cutoff:%B %d, %Y}.")
            elif exceeds_monthly_cap(acting_employee, start, days):
                form.add_error(None, "This would exceed the 5-CTO-day-per-month usage cap.")
            elif forms_illegal_consecutive_run(acting_employee, start, end):
                form.add_error(None, "CTO cannot be taken for 3 or more consecutive days.")
            else:
                application = form.save(commit=False)
                application.employee = acting_employee
                application.full_clean()
                application.save()
                CTOUsageApplicationAction.objects.create(
                    application=application, action="submit",
                    resulting_status=application.status, acted_by=request.user,
                )
                notify_status_change(application)
                messages.success(request, "CTO application submitted.")
                return redirect("cto:my_cto")
    else:
        form = CTOUsageApplicationForm(employee=acting_employee)

    balance = compute_available_cto_balance(acting_employee)
    return render(request, "cto/cto_apply.html", {"form": form, "balance": balance})


@login_required
def cto_queue(request):
    acting_employee = get_acting_employee(request.user)
    applications = visible_cto_applications_for(acting_employee).select_related("employee")
    return render(request, "cto/cto_queue.html", {"applications": applications, "is_hr": is_hr(acting_employee)})


def _finalize_success(application, actor_user):
    """
    Mirrors leave.views._finalize_success: the balance check happens once,
    at the single point of final commitment (COH approval — CTO usage has
    no data-entry-only shortcut), not at earlier stages that might still
    be returned or rejected.
    """
    available = compute_available_cto_balance(application.employee, as_of_date=application.start_date)
    if application.number_of_days > available:
        return f"Insufficient CTO balance: {available} day(s) available, {application.number_of_days} requested."
    CTOCreditTransaction.objects.create(
        employee=application.employee,
        transaction_type=CTOCreditTransaction.USED,
        days=-application.number_of_days,
        transaction_date=application.start_date,
        usage_application=application,
        created_by=actor_user,
    )
    return None


@login_required
def cto_action(request, pk):
    """Single POST endpoint for every CTO routing step — same dispatch
    shape as leave.views.leave_action."""
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(CTOUsageApplication, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()

    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    status = application.status

    def apply_transition(new_status, action_name):
        application.status = new_status
        application.save(update_fields=["status", "updated_at"])
        CTOUsageApplicationAction.objects.create(
            application=application, action=action_name, resulting_status=new_status,
            notes=notes, acted_by=request.user,
        )
        notify_status_change(application)

    allowed = False
    error = None

    if status == CTOUsageApplication.SUBMITTED and is_supervisor_of(acting_employee, application.employee):
        if action == "endorse":
            allowed = True
            apply_transition(CTOUsageApplication.ENDORSED_BY_SUPERVISOR, "endorse")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                CTOUsageApplication.REJECTED if action == "reject" else CTOUsageApplication.RETURNED, action
            )
    elif status == CTOUsageApplication.ENDORSED_BY_SUPERVISOR and is_hr(acting_employee):
        if action == "process":
            allowed = True
            apply_transition(CTOUsageApplication.PROCESSED_BY_HR, "process")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                CTOUsageApplication.REJECTED if action == "reject" else CTOUsageApplication.RETURNED, action
            )
    elif status == CTOUsageApplication.PROCESSED_BY_HR and is_administrative_officer(acting_employee):
        if action == "recommend":
            allowed = True
            apply_transition(CTOUsageApplication.RECOMMENDED_BY_AO, "recommend")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                CTOUsageApplication.REJECTED if action == "reject" else CTOUsageApplication.RETURNED, action
            )
    elif status == CTOUsageApplication.RECOMMENDED_BY_AO and is_chief_of_hospital(acting_employee):
        if action == "approve":
            error = _finalize_success(application, request.user)
            if error is None:
                allowed = True
                apply_transition(CTOUsageApplication.APPROVED, "approve")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                CTOUsageApplication.REJECTED if action == "reject" else CTOUsageApplication.RETURNED, action
            )

    if error:
        messages.error(request, error)
    elif allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this application.")

    return redirect("cto:cto_queue")


@login_required
def print_cto_form(request, pk):
    """Form No. BDH-ADM-HR-01F04-B (see cto/cto_form.py)."""
    from .cto_form import render_pdf

    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(CTOUsageApplication, pk=pk)
    if not can_view_cto_application(acting_employee, application):
        raise PermissionDenied("You are not authorized to view this CTO application.")
    return pdf_response(
        request, lambda: render_pdf(application, printed_by=request.user),
        f"CTO-Application_{application.employee.surname}_{application.pk}.pdf", "cto:my_cto",
    )
