from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from printouts.http import pdf_response

from .forms import DutyExchangeRequestForm
from .models import DutyExchangeRequest, DutyExchangeRequestAction
from .notifications import notify_status_change
from .permissions import (
    can_view_exchange_request,
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_hr,
    is_supervisor_of_either_party,
    visible_exchange_requests_for,
)


@login_required
def exchange_apply(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    if request.method == "POST":
        form = DutyExchangeRequestForm(request.POST, employee_a=acting_employee)
        if form.is_valid():
            exchange_request = form.save(commit=False)
            exchange_request.employee_a = acting_employee
            exchange_request.full_clean()
            exchange_request.save()
            DutyExchangeRequestAction.objects.create(
                request=exchange_request, action="file", resulting_status=exchange_request.status,
                acted_by=request.user,
            )
            notify_status_change(exchange_request)
            messages.success(
                request, f"Exchange request filed. Awaiting {exchange_request.employee_b}'s consent."
            )
            return redirect("exchange:my_exchanges")
    else:
        form = DutyExchangeRequestForm(employee_a=acting_employee)

    return render(request, "exchange/exchange_apply.html", {"form": form})


@login_required
def my_exchanges(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    requests = (
        DutyExchangeRequest.objects.filter(employee_a=acting_employee)
        | DutyExchangeRequest.objects.filter(employee_b=acting_employee)
    ).distinct()
    pending_my_consent = requests.filter(status=DutyExchangeRequest.PENDING_CONSENT, employee_b=acting_employee)
    return render(
        request, "exchange/my_exchanges.html",
        {"requests": requests, "pending_my_consent": pending_my_consent},
    )


@login_required
def exchange_consent(request, pk):
    """Employee B's mutual-consent step (§8) — must happen before the
    request enters the Supervisor/HR/AO/COH routing chain."""
    acting_employee = get_acting_employee(request.user)
    exchange_request = get_object_or_404(DutyExchangeRequest, pk=pk)

    if acting_employee is None or acting_employee.pk != exchange_request.employee_b_id:
        raise PermissionDenied("Only the named Employee B may consent to this request.")
    if exchange_request.status != DutyExchangeRequest.PENDING_CONSENT:
        raise PermissionDenied("This request is no longer awaiting consent.")

    decision = request.POST.get("decision")
    if decision == "consent":
        from django.utils import timezone

        exchange_request.status = DutyExchangeRequest.SUBMITTED
        exchange_request.consented_at = timezone.now()
        exchange_request.save(update_fields=["status", "consented_at", "updated_at"])
        DutyExchangeRequestAction.objects.create(
            request=exchange_request, action="consent", resulting_status=exchange_request.status,
            acted_by=request.user,
        )
        notify_status_change(exchange_request)
        messages.success(request, "Consent recorded. Request submitted for Supervisor endorsement.")
    elif decision == "decline":
        exchange_request.status = DutyExchangeRequest.CONSENT_DECLINED
        exchange_request.save(update_fields=["status", "updated_at"])
        DutyExchangeRequestAction.objects.create(
            request=exchange_request, action="decline", resulting_status=exchange_request.status,
            acted_by=request.user,
        )
        notify_status_change(exchange_request)
        messages.info(request, "Consent declined. The request will not proceed.")
    else:
        raise PermissionDenied("Invalid decision.")

    return redirect("exchange:my_exchanges")


@login_required
def exchange_queue(request):
    acting_employee = get_acting_employee(request.user)
    requests = visible_exchange_requests_for(acting_employee).select_related("employee_a", "employee_b")
    return render(request, "exchange/exchange_queue.html", {"requests": requests})


@login_required
def exchange_action(request, pk):
    """Single POST endpoint for every routing step, same dispatch shape as
    leave.views.leave_action / cto.views.cto_action. No balance to check
    at final approval — an exchange is one-for-one by construction (see
    models.py), so COH approval is a plain status transition."""
    acting_employee = get_acting_employee(request.user)
    exchange_request = get_object_or_404(DutyExchangeRequest, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()

    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    status = exchange_request.status

    def apply_transition(new_status, action_name):
        exchange_request.status = new_status
        exchange_request.save(update_fields=["status", "updated_at"])
        DutyExchangeRequestAction.objects.create(
            request=exchange_request, action=action_name, resulting_status=new_status,
            notes=notes, acted_by=request.user,
        )
        notify_status_change(exchange_request)

    allowed = False

    if status == DutyExchangeRequest.SUBMITTED and is_supervisor_of_either_party(acting_employee, exchange_request):
        if action == "endorse":
            allowed = True
            apply_transition(DutyExchangeRequest.ENDORSED_BY_SUPERVISOR, "endorse")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                DutyExchangeRequest.REJECTED if action == "reject" else DutyExchangeRequest.RETURNED, action
            )
    elif status == DutyExchangeRequest.ENDORSED_BY_SUPERVISOR and is_hr(acting_employee):
        if action == "process":
            allowed = True
            apply_transition(DutyExchangeRequest.PROCESSED_BY_HR, "process")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                DutyExchangeRequest.REJECTED if action == "reject" else DutyExchangeRequest.RETURNED, action
            )
    elif status == DutyExchangeRequest.PROCESSED_BY_HR and is_administrative_officer(acting_employee):
        if action == "recommend":
            allowed = True
            apply_transition(DutyExchangeRequest.RECOMMENDED_BY_AO, "recommend")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                DutyExchangeRequest.REJECTED if action == "reject" else DutyExchangeRequest.RETURNED, action
            )
    elif status == DutyExchangeRequest.RECOMMENDED_BY_AO and is_chief_of_hospital(acting_employee):
        if action == "approve":
            allowed = True
            apply_transition(DutyExchangeRequest.APPROVED, "approve")
        elif action in ("reject", "return"):
            allowed = True
            apply_transition(
                DutyExchangeRequest.REJECTED if action == "reject" else DutyExchangeRequest.RETURNED, action
            )

    if allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this request.")

    return redirect("exchange:exchange_queue")


@login_required
def print_exchange_form(request, pk):
    """Form No. BDH-ADM-HR-01F04-C (see exchange/exchange_form.py)."""
    from .exchange_form import render_pdf

    acting_employee = get_acting_employee(request.user)
    req = get_object_or_404(DutyExchangeRequest, pk=pk)
    if not can_view_exchange_request(acting_employee, req):
        raise PermissionDenied("You are not authorized to view this exchange request.")
    return pdf_response(
        request, lambda: render_pdf(req, printed_by=request.user),
        f"Exchange-of-Duty_{req.employee_a.surname}_{req.pk}.pdf", "exchange:my_exchanges",
    )
