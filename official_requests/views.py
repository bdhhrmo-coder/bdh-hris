from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import OfficialRequestForm
from .models import OfficialRequest, OfficialRequestAction
from .notifications import notify_status_change
from .permissions import actor_may_advance, get_acting_employee, visible_requests_for
from accounts.models import RoleAssignment

from .routing import action_name_for, next_status_for


@login_required
def request_apply(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    if request.method == "POST":
        form = OfficialRequestForm(request.POST)
        if form.is_valid():
            official_request = form.save(commit=False)
            official_request.employee = acting_employee
            official_request.recorded_only = acting_employee.has_role(RoleAssignment.CHIEF_OF_HOSPITAL)
            official_request.full_clean()
            official_request.save()
            OfficialRequestAction.objects.create(
                request=official_request, action="submit", resulting_status=official_request.status,
                acted_by=request.user,
            )
            notify_status_change(official_request)
            if official_request.recorded_only:
                messages.success(request, f"{official_request.get_request_type_display()} request submitted. "
                                          "HR will record it; the approval itself is outside BDH (PHO/Capitol).")
            else:
                messages.success(request, f"{official_request.get_request_type_display()} request submitted.")
            return redirect("official_requests:my_requests")
    else:
        form = OfficialRequestForm()

    return render(request, "official_requests/request_apply.html", {"form": form})


@login_required
def my_requests(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    requests = acting_employee.official_requests.exclude(status=OfficialRequest.CANCELLED, batch__isnull=False)
    return render(request, "official_requests/my_requests.html", {"requests": requests})


@login_required
def request_queue(request):
    acting_employee = get_acting_employee(request.user)
    requests = list(visible_requests_for(acting_employee).select_related("employee").order_by("submitted_at", "pk"))  # oldest first: first filed, first acted on (owner, 2026-10-06)
    for r in requests:
        r.next_action = action_name_for(next_status_for(r))
    from . import batch as batch_rules

    batches = batch_rules.batches_awaiting(acting_employee, request.user)
    return render(request, "official_requests/request_queue.html", {
        "requests": requests, "batches": batches, "can_file_batch": bool(batch_rules.filer_roles(acting_employee)),
    })


@login_required
def request_action(request, pk):
    """Single POST endpoint for every routing step, generalized across all
    four request types via routing.next_status() rather than a hand-written
    branch tree per type (see routing.py)."""
    acting_employee = get_acting_employee(request.user)
    official_request = get_object_or_404(OfficialRequest, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()

    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    target_status = next_status_for(official_request)
    expected_action = action_name_for(target_status) if target_status else None

    allowed = False
    if expected_action is not None and actor_may_advance(acting_employee, official_request):
        if action == expected_action:
            allowed = True
            official_request.status = target_status
            official_request.save(update_fields=["status", "updated_at"])
            OfficialRequestAction.objects.create(
                request=official_request, action=action, resulting_status=target_status,
                notes=notes, acted_by=request.user,
            )
            notify_status_change(official_request)
        elif action in ("reject", "return"):
            new_status = OfficialRequest.REJECTED if action == "reject" else OfficialRequest.RETURNED
            allowed = True
            official_request.status = new_status
            official_request.save(update_fields=["status", "updated_at"])
            OfficialRequestAction.objects.create(
                request=official_request, action=action, resulting_status=new_status,
                notes=notes, acted_by=request.user,
            )
            notify_status_change(official_request)

    if allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this request.")

    return redirect("official_requests:request_queue")
