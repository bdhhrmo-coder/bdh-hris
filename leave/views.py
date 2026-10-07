from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from employees.permissions import get_acting_employee

from .balance_cards import balance_cards
from .balances import compute_available_balance
from .csc_form6 import render_pdf as render_csc_form6_pdf
from .cosp_leave_form import render_pdf as render_cosp_leave_form_pdf
from .forms import LeaveApplicationForm
from .models import LeaveApplication, LeaveApplicationAction, LeaveCreditTransaction, LeaveType
from .notifications import notify_status_change
from .permissions import (
    can_view_application,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
    is_supervisor_of,
    visible_applications_for,
)


@login_required
def leave_apply(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    if request.method == "POST":
        form = LeaveApplicationForm(request.POST, employee=acting_employee)
        if form.is_valid():
            application = form.save(commit=False)
            application.employee = acting_employee
            application.save()
            LeaveApplicationAction.objects.create(
                application=application,
                action="submit",
                resulting_status=application.status,
                acted_by=request.user,
            )
            notify_status_change(application)
            messages.success(request, "Leave application submitted.")
            return redirect("leave:my_applications")
    else:
        form = LeaveApplicationForm(employee=acting_employee)

    selected = form["leave_type"].value() or ""
    return render(request, "leave/leave_apply.html",
                  {"form": form, "cards": balance_cards(acting_employee), "selected_type": str(selected)})


@login_required
def my_applications(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    applications = acting_employee.leave_applications.select_related("leave_type")
    return render(request, "leave/my_applications.html", {"applications": applications})


@login_required
def leave_queue(request):
    acting_employee = get_acting_employee(request.user)
    applications = visible_applications_for(acting_employee).select_related("employee", "leave_type").order_by(
        "submitted_at", "pk"
    )  # oldest first: first filed, first acted on (owner, 2026-10-06)
    return render(request, "leave/leave_queue.html", {"applications": applications})


def _finalize_success(application, actor_user):
    """
    Called exactly once, at the step that finally commits a leave
    application (RECORDED for data-entry-only types, APPROVED for COSP
    Leave). This is where the balance is actually checked and the ledger
    usage entry is posted — the "check balance before allowing" gate
    lives here, at the single point of commitment, not earlier stages
    that might still be reversed.

    Returns an error string if the balance check fails, else None.
    """
    leave_type = application.leave_type
    if leave_type.balance_tracking != LeaveType.TRACKING_UNTRACKED:
        available = compute_available_balance(application.employee, leave_type, as_of_date=application.start_date)
        if available is not None and application.number_of_days > available:
            return (
                f"Insufficient {leave_type.name} balance: {available} day(s) available, "
                f"{application.number_of_days} requested."
            )
        LeaveCreditTransaction.objects.create(
            employee=application.employee,
            leave_type=leave_type,
            transaction_type=LeaveCreditTransaction.USED,
            days=-application.number_of_days,
            transaction_date=application.start_date,
            leave_application=application,
            created_by=actor_user,
        )
    return None


@login_required
def leave_action(request, pk):
    """
    Single POST endpoint for every routing step. `action` in POST decides
    what happens; which actions are allowed depends on the acting
    employee's role AND the application's current status AND (critically)
    whether its leave_type.requires_full_routing — a non-COSP-Leave
    application can only ever go SUBMITTED -> RECORDED/REJECTED, no matter
    what action name is posted.
    """
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()

    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    full_routing = application.leave_type.requires_full_routing
    status = application.status

    def apply_transition(new_status, action_name):
        application.status = new_status
        application.save(update_fields=["status", "updated_at"])
        LeaveApplicationAction.objects.create(
            application=application, action=action_name, resulting_status=new_status,
            notes=notes, acted_by=request.user,
        )
        notify_status_change(application)

    allowed = False
    error = None

    if not full_routing:
        if status == LeaveApplication.SUBMITTED and is_hr(acting_employee):
            if action == "record":
                error = _finalize_success(application, request.user)
                if error is None:
                    allowed = True
                    apply_transition(LeaveApplication.RECORDED, "record")
            elif action == "reject":
                allowed = True
                apply_transition(LeaveApplication.REJECTED, "reject")
    else:
        if status == LeaveApplication.SUBMITTED and is_supervisor_of(acting_employee, application.employee):
            if action == "endorse":
                allowed = True
                apply_transition(LeaveApplication.ENDORSED_BY_SUPERVISOR, "endorse")
            elif action in ("reject", "return"):
                allowed = True
                apply_transition(
                    LeaveApplication.REJECTED if action == "reject" else LeaveApplication.RETURNED, action
                )
        elif status == LeaveApplication.ENDORSED_BY_SUPERVISOR and is_hr(acting_employee):
            if action == "process":
                allowed = True
                apply_transition(LeaveApplication.PROCESSED_BY_HR, "process")
            elif action in ("reject", "return"):
                allowed = True
                apply_transition(
                    LeaveApplication.REJECTED if action == "reject" else LeaveApplication.RETURNED, action
                )
        elif status == LeaveApplication.PROCESSED_BY_HR and is_administrative_officer(acting_employee):
            if action == "recommend":
                allowed = True
                apply_transition(LeaveApplication.RECOMMENDED_BY_AO, "recommend")
            elif action in ("reject", "return"):
                allowed = True
                apply_transition(
                    LeaveApplication.REJECTED if action == "reject" else LeaveApplication.RETURNED, action
                )
        elif status == LeaveApplication.RECOMMENDED_BY_AO and is_chief_of_hospital(acting_employee):
            if action == "approve":
                error = _finalize_success(application, request.user)
                if error is None:
                    allowed = True
                    apply_transition(LeaveApplication.APPROVED, "approve")
            elif action in ("reject", "return"):
                allowed = True
                apply_transition(
                    LeaveApplication.REJECTED if action == "reject" else LeaveApplication.RETURNED, action
                )

    if error:
        messages.error(request, error)
    elif allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this application.")

    return redirect("leave:leave_queue")


def _pdf_unavailable(request, exc):
    """PDF conversion needs LibreOffice on the server (see
    leave/pdf_convert.py). If it's missing or fails, tell the user plainly
    instead of showing a bare "Server Error (500)"; the detail goes to the
    service log for the System Administrator."""
    import logging

    logging.getLogger(__name__).error("Leave form PDF conversion failed: %s", exc)
    messages.error(
        request,
        "The printable form could not be generated right now. Please contact your System "
        "Administrator (the server's PDF converter may not be installed).",
    )
    return redirect("leave:my_applications")


@login_required
def print_csc_form6(request, pk):
    """
    Renders the official CSC Form No. 6 for this application as a PDF.
    COSP Leave doesn't use this form (it gets its own custom BDH form per
    CLAUDE.md §6.3), so it's refused here rather than silently printing the
    wrong form.
    """
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication, pk=pk)

    if not can_view_application(acting_employee, application):
        raise PermissionDenied("You are not authorized to view this application.")

    if application.leave_type.requires_full_routing:
        raise PermissionDenied("COSP Leave prints on the custom COSP leave form, not CSC Form 6.")

    try:
        pdf_bytes = render_csc_form6_pdf(application)
    except RuntimeError as exc:
        return _pdf_unavailable(request, exc)
    filename = f"CSC-Form-6_{application.employee.surname}_{application.pk}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
def print_cosp_leave_form(request, pk):
    """
    Renders the custom BDH COSP Leave form for this application as a PDF.
    Only COSP Leave itself prints here — Wellness/Emergency Leave availed
    by a COSP employee still prints on CSC Form 6.
    """
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication, pk=pk)

    if not can_view_application(acting_employee, application):
        raise PermissionDenied("You are not authorized to view this application.")

    if not application.leave_type.requires_full_routing:
        raise PermissionDenied("This leave type prints on CSC Form 6, not the COSP leave form.")

    try:
        pdf_bytes = render_cosp_leave_form_pdf(application, printed_by=request.user)
    except RuntimeError as exc:
        return _pdf_unavailable(request, exc)
    filename = f"COSP-Leave-Form_{application.employee.surname}_{application.pk}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
