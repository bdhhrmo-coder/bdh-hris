from datetime import date

from leave.permissions import RETURN_NEEDS_REMARK
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
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    UNIT_STEP,
    can_endorse,
    can_endorse_second,
    supervisor_step_result,
    can_record_capitol_decision,
    can_view_application,
    capitol_applications_for,
    coh_filed_regular,
    visible_applications_for,
)


def _render_form(request, form, acting_employee, application=None):
    import json

    selected = form["leave_type"].value() or ""
    return render(request, "leave/leave_apply.html", {
        "form": form, "cards": balance_cards(acting_employee), "selected_type": str(selected),
        "type_codes": json.dumps(form.type_codes()), "application": application,
        "is_cosp": acting_employee.employment_status == "COSP",
    })


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

    return _render_form(request, form, acting_employee)


@login_required
def leave_edit(request, pk):
    """Batch 8: the filer corrects their own application before anyone acts
    on it (Submitted), or after it is returned. A returned application is
    resubmitted and starts again from the first step, as with attendance
    corrections. No approval rule changes."""
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication, pk=pk)
    if acting_employee is None or application.employee_id != acting_employee.pk:
        raise PermissionDenied("Only the employee who filed this application can edit it.")
    if not application.is_editable_by_filer():
        messages.error(request, "This application can no longer be edited: it is already being processed.")
        return redirect("leave:leave_detail", pk=pk)

    if request.method == "POST":
        form = LeaveApplicationForm(request.POST, instance=application, employee=acting_employee)
        if form.is_valid():
            was_returned = application.status == LeaveApplication.RETURNED
            application = form.save(commit=False)
            application.status = LeaveApplication.SUBMITTED
            application.save()
            LeaveApplicationAction.objects.create(
                application=application, action="resubmit" if was_returned else "edit",
                resulting_status=application.status, acted_by=request.user,
            )
            if was_returned:
                notify_status_change(application)
            messages.success(request, "Leave application resubmitted." if was_returned else "Leave application updated.")
            return redirect("leave:leave_detail", pk=application.pk)
    else:
        form = LeaveApplicationForm(instance=application, employee=acting_employee)
    return _render_form(request, form, acting_employee, application)


@login_required
def leave_detail(request, pk):
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication.objects.select_related("employee", "leave_type"), pk=pk)
    if not can_view_application(acting_employee, application):
        raise PermissionDenied("You are not authorized to view this application.")
    return render(request, "leave/leave_detail.html", {
        "a": application,
        "history": application.actions.select_related("acted_by__employee"),
        "can_edit": acting_employee is not None and application.employee_id == acting_employee.pk
        and application.is_editable_by_filer(),
        "can_record_capitol": can_record_capitol_decision(acting_employee, application),
    })


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
    at_capitol = capitol_applications_for(acting_employee).select_related("employee", "leave_type").order_by(
        "submitted_at", "pk")
    return render(request, "leave/leave_queue.html", {"applications": applications, "at_capitol": at_capitol})


def _finalize_success(application, actor_user):
    """
    Called exactly once per cycle, at the step that takes the days: COH
    approval for the COSP route, HR "record and verify" for the regular
    route (owner 2026-11-03). This is where the balance is actually checked
    and the ledger usage entry is posted.

    Returns an error string if the balance check fails, else None.
    """
    leave_type = application.leave_type
    if leave_type.balance_tracking != LeaveType.TRACKING_UNTRACKED:
        # Monetization / terminal leave has no inclusive dates: use the filing date.
        as_of = application.start_date or application.submitted_at.date()
        available = compute_available_balance(application.employee, leave_type, as_of_date=as_of)
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
            transaction_date=as_of,
            leave_application=application,
            created_by=actor_user,
        )
    return None


def _restore_credits(application, actor_user, reason):
    """Gives back the days taken for this application, if any are still
    taken (owner 2026-11-03: restored automatically when a regular leave is
    returned or rejected inside BDH or by the Capitol). Adds a ledger line;
    nothing is deleted. Safe to call when nothing was taken."""
    from django.db.models import Sum

    entries = LeaveCreditTransaction.objects.filter(leave_application=application)
    net = entries.aggregate(t=Sum("days"))["t"] or 0
    if net >= 0:
        return
    used = entries.filter(transaction_type=LeaveCreditTransaction.USED).order_by("-pk").first()
    LeaveCreditTransaction.objects.create(
        employee=application.employee, leave_type=application.leave_type,
        transaction_type=LeaveCreditTransaction.ADJUSTMENT, days=-net,
        transaction_date=used.transaction_date if used else date.today(),
        leave_application=application, notes=f"Restored: {reason}"[:255], created_by=actor_user,
    )


@login_required
def leave_action(request, pk):
    """
    Single POST endpoint for every routing step. `action` in POST decides
    what happens; which actions are allowed depends on the acting
    employee's role, the application's current status and its route
    (owner decisions 2026-11-03).
    """
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()
    back = request.POST.get("next") == "detail"

    def done():
        return redirect("leave:leave_detail", pk=application.pk) if back else redirect("leave:leave_queue")

    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    # A return or rejection always carries a remark (owner requests 2026-11-03).
    if action in ("return", "reject", "capitol_return", "capitol_reject") and not notes:
        messages.error(request, RETURN_NEEDS_REMARK)
        return done()

    A = LeaveApplication
    regular = application.route == A.ROUTE_REGULAR
    status = application.status
    filers = [application.employee]  # never acts on their own (owner decision 2026-10-26)

    def apply_transition(new_status, action_name):
        application.status = new_status
        application.save(update_fields=["status", "updated_at"])
        LeaveApplicationAction.objects.create(
            application=application, action=action_name, resulting_status=new_status,
            notes=notes, acted_by=request.user,
        )
        if new_status in (A.RETURNED, A.REJECTED):
            _restore_credits(application, request.user, f"{action_name.replace('_', ' ')} - {notes}")
        notify_status_change(application)

    def stop(action_name):
        """Return or reject at an internal step."""
        apply_transition(A.REJECTED if action_name == "reject" else A.RETURNED, action_name)

    allowed = False
    error = None

    if status == A.SUBMITTED and regular and coh_filed_regular(application) \
            and can_act_hr_step(acting_employee, filers, application):
        # The COH's own regular leave: HR records and verifies, then it goes to the Capitol.
        if action == "record":
            error = _finalize_success(application, request.user)
            if error is None:
                allowed = True
                apply_transition(A.FOR_CAPITOL_APPROVAL, "record")
        elif action == "return":
            allowed = True
            stop(action)
    elif status == A.SUBMITTED and can_endorse(acting_employee, filers):
        if action == "endorse":
            allowed = True
            # Two-level endorsement (2026-10-27): the unit head first, then the section head.
            if supervisor_step_result(acting_employee, filers) == UNIT_STEP:
                apply_transition(A.ENDORSED_BY_UNIT_HEAD, "endorse_unit")
            else:
                apply_transition(A.ENDORSED_BY_SUPERVISOR, "endorse")
        elif action in ("reject", "return"):
            allowed = True
            stop(action)
    elif status == A.ENDORSED_BY_UNIT_HEAD and can_endorse_second(acting_employee, filers):
        if action == "endorse":
            allowed = True
            apply_transition(A.ENDORSED_BY_SUPERVISOR, "endorse")
        elif action in ("reject", "return"):
            allowed = True
            stop(action)
    elif status == A.ENDORSED_BY_SUPERVISOR and can_act_hr_step(acting_employee, filers, application):
        if regular and action == "record":
            # Regular leave: HR records and verifies; the days are taken here.
            error = _finalize_success(application, request.user)
            if error is None:
                allowed = True
                apply_transition(A.PROCESSED_BY_HR, "record")
        elif not regular and action == "process":
            allowed = True
            apply_transition(A.PROCESSED_BY_HR, "process")
        elif action == "return" or (action == "reject" and not regular):  # HR never rejects regular leave
            allowed = True
            stop(action)
    elif status == A.PROCESSED_BY_HR and can_act_ao_step(acting_employee, filers):
        if action == ("review" if regular else "recommend"):
            allowed = True
            apply_transition(A.RECOMMENDED_BY_AO, action)
        elif action in ("reject", "return"):
            allowed = True
            stop(action)
    elif status == A.RECOMMENDED_BY_AO and can_act_coh_step(acting_employee, filers):
        if regular and action == "recommend_capitol":
            allowed = True
            apply_transition(A.FOR_CAPITOL_APPROVAL, "recommend_capitol")
        elif not regular and action == "approve":
            error = _finalize_success(application, request.user)
            if error is None:
                allowed = True
                apply_transition(A.APPROVED, "approve")
        elif action in ("reject", "return"):
            allowed = True
            stop(action)
    elif can_record_capitol_decision(acting_employee, application):
        # HR records what the Capitol decided, when it returns or rejects it.
        if action == "capitol_return":
            allowed = True
            apply_transition(A.RETURNED, "capitol_return")
        elif action == "capitol_reject":
            allowed = True
            apply_transition(A.REJECTED, "capitol_reject")

    if error:
        messages.error(request, error)
    elif allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this application.")

    return done()


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

    if application.is_cosp_route:
        raise PermissionDenied("COSP employees' leave prints on the COSP leave form (01F04-A), not CSC Form 6.")

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
    Renders the custom BDH COSP leave form (01F04-A) as a PDF: every leave
    of a COSP employee, incl. Wellness and Emergency Leave (owner
    2026-11-03). Regular employees' leave prints on CSC Form 6.
    """
    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(LeaveApplication, pk=pk)

    if not can_view_application(acting_employee, application):
        raise PermissionDenied("You are not authorized to view this application.")

    if not application.is_cosp_route:
        raise PermissionDenied("Regular employees' leave prints on CSC Form 6, not the COSP leave form.")

    try:
        pdf_bytes = render_cosp_leave_form_pdf(application, printed_by=request.user)
    except RuntimeError as exc:
        return _pdf_unavailable(request, exc)
    filename = f"COSP-Leave-Form_{application.employee.surname}_{application.pk}.pdf"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
