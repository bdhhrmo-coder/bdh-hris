from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import OfficialRequestForm
from .models import OfficialRequest, OfficialRequestAction
from .permissions import actor_may_advance, get_acting_employee, visible_requests_for
from .routing import action_name_for, next_status


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
            official_request.full_clean()
            official_request.save()
            OfficialRequestAction.objects.create(
                request=official_request, action="submit", resulting_status=official_request.status,
                acted_by=request.user,
            )
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
    requests = acting_employee.official_requests.all()
    return render(request, "official_requests/my_requests.html", {"requests": requests})


@login_required
def request_queue(request):
    acting_employee = get_acting_employee(request.user)
    requests = list(visible_requests_for(acting_employee).select_related("employee"))
    for r in requests:
        r.next_action = action_name_for(next_status(r.request_type, r.status))
    return render(request, "official_requests/request_queue.html", {"requests": requests})


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

    target_status = next_status(official_request.request_type, official_request.status)
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
        elif action in ("reject", "return"):
            new_status = OfficialRequest.REJECTED if action == "reject" else OfficialRequest.RETURNED
            allowed = True
            official_request.status = new_status
            official_request.save(update_fields=["status", "updated_at"])
            OfficialRequestAction.objects.create(
                request=official_request, action=action, resulting_status=new_status,
                notes=notes, acted_by=request.user,
            )

    if allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this request.")

    return redirect("official_requests:request_queue")
