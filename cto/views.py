from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .balances import (
    compute_available_cto_balance,
    exceeds_monthly_cap,
    forms_illegal_consecutive_run,
    violates_filing_deadline,
    violates_usage_cutoff,
)
from .forms import CTOCreditEntryForm, CTOUsageApplicationForm
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


@login_required
def credit_entry_create(request):
    """HR's direct-entry screen for a verified OT/rest-day/holiday claim."""
    acting_employee = get_acting_employee(request.user)
    if not is_hr(acting_employee):
        raise PermissionDenied("Only HR may record a CTO credit entry.")

    if request.method == "POST":
        form = CTOCreditEntryForm(request.POST)
        if form.is_valid():
            work_date = form.cleaned_data["work_date"]
            if violates_filing_deadline(work_date, date.today()):
                form.add_error(
                    "work_date",
                    f"This claim is for work in {work_date.year} but the November 30, {work_date.year} "
                    "filing deadline has passed. A Chief of Hospital exception is required.",
                )
            else:
                entry = form.save(commit=False)
                entry.multiplier_applied = form.cleaned_data["_multiplier_applied"]
                entry.credited_hours = form.cleaned_data["_credited_hours"]
                entry.credited_days = form.cleaned_data["_credited_days"]
                entry.shift_hours_used = form.cleaned_data["_shift_hours_used"]
                entry.recorded_by = request.user
                entry.full_clean()
                entry.save()
                CTOCreditTransaction.objects.create(
                    employee=entry.employee,
                    transaction_type=CTOCreditTransaction.EARNED,
                    days=entry.credited_days,
                    transaction_date=entry.work_date,
                    credit_entry=entry,
                    created_by=request.user,
                )
                notify_credit_entry(entry)
                messages.success(request, f"Credited {entry.credited_days} CTO day(s) to {entry.employee}.")
                return redirect("cto:credit_entry_create")
    else:
        form = CTOCreditEntryForm()
    return render(request, "cto/credit_entry_form.html", {"form": form})


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
                form.add_error("end_date", f"CTO usage cannot extend past December 15, {end.year}.")
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
    return render(request, "cto/cto_queue.html", {"applications": applications})


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
