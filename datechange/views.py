from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from cto.models import CTOUsageApplication
from cto.permissions import can_view_cto_application
from employees.permissions import get_acting_employee
from leave.models import LeaveApplication
from leave.permissions import RETURN_NEEDS_REMARK, can_view_application

from .forms import DateChangeForm
from .models import DateChangeAction, DateChangeRequest as DC
from .notifications import notify_change
from .rules import (cover_for, date_change_block, final_check, kind_of, may_reject, new_dates_error, next_step,
                    shifted_cover)


def _target(kind, pk):
    if kind == "leave":
        return get_object_or_404(LeaveApplication.objects.select_related("employee", "leave_type"), pk=pk)
    if kind == "cto":
        return get_object_or_404(CTOUsageApplication.objects.select_related("employee"), pk=pk)
    raise PermissionDenied("Unknown request type.")


def _target_url(target):
    if isinstance(target, LeaveApplication):
        return redirect("leave:leave_detail", pk=target.pk)
    return redirect("cto:cto_detail", pk=target.pk)


def _can_view(acting_employee, change):
    target = change.target
    if isinstance(target, LeaveApplication):
        return can_view_application(acting_employee, target)
    return can_view_cto_application(acting_employee, target)


@login_required
def change_new(request, kind, pk):
    """The employee asks to move their own approved leave or CTO."""
    acting_employee = get_acting_employee(request.user)
    target = _target(kind, pk)
    today = timezone.localdate()
    block = date_change_block(acting_employee, target, today)
    if block:
        messages.error(request, block)
        return _target_url(target)

    form = DateChangeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_start, new_end = form.cleaned_data["new_start_date"], form.cleaned_data["new_end_date"]
        change = None
        with transaction.atomic():
            # Lock the original so two changes filed at once cannot both pass.
            locked = type(target).objects.select_for_update().get(pk=target.pk)
            block = date_change_block(acting_employee, locked, today)
            error = block or new_dates_error(locked, new_start, new_end, today)
            if error:
                form.add_error(None, error)
            else:
                change = DC.objects.create(
                    kind=kind_of(locked),
                    leave_application=locked if kind == "leave" else None,
                    cto_application=locked if kind == "cto" else None,
                    employee=locked.employee,
                    old_start_date=locked.start_date, old_end_date=locked.end_date,
                    new_start_date=new_start, new_end_date=new_end,
                    number_of_days=locked.number_of_days,
                    justification=form.cleaned_data["justification"],
                )
                DateChangeAction.objects.create(request=change, action="submit", resulting_status=change.status,
                                                notes=form.cleaned_data["justification"][:255],
                                                acted_by=request.user)
        if change is not None:
            notify_change(change)
            messages.success(request, "Date change requested. Your approved dates stay in force until it is approved.")
            return redirect("datechange:detail", pk=change.pk)
    return render(request, "datechange/change_new.html", {"form": form, "target": target, "kind": kind})


@login_required
def change_detail(request, pk):
    acting_employee = get_acting_employee(request.user)
    change = get_object_or_404(DC.objects.select_related("employee"), pk=pk)
    if not _can_view(acting_employee, change):
        raise PermissionDenied("You are not authorized to view this date change request.")
    step = next_step(change, acting_employee)
    return render(request, "datechange/change_detail.html", {
        "c": change, "step": step, "can_reject": step is not None and may_reject(change),
        "history": change.actions.select_related("acted_by__employee"),
        "target_url": ("leave:leave_detail" if change.kind == DC.KIND_LEAVE else "cto:cto_detail"),
    })


@login_required
def change_action(request, pk):
    """One POST endpoint for every step, as for leave and CTO."""
    acting_employee = get_acting_employee(request.user)
    change = get_object_or_404(DC, pk=pk)
    if request.method != "POST":
        return redirect("datechange:detail", pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()[:255]
    step = next_step(change, acting_employee)
    if step is None:
        raise PermissionDenied("You are not authorized to act on this date change request.")
    if action in ("return", "reject") and not notes:
        messages.error(request, RETURN_NEEDS_REMARK)
        return redirect("datechange:detail", pk=pk)
    if action == "reject" and not may_reject(change):
        raise PermissionDenied("HR may return a regular leave, not reject it.")

    if action == "return":
        new_status = DC.RETURNED
    elif action == "reject":
        new_status = DC.REJECTED
    elif action == step[0]:
        new_status = step[1]
    else:
        raise PermissionDenied("Unknown action.")

    with transaction.atomic():
        change = DC.objects.select_for_update().get(pk=pk)
        if new_status == DC.APPLIED:
            target = type(change.target).objects.select_for_update().get(pk=change.target.pk)
            error = final_check(change, timezone.localdate())
            if error:
                messages.error(request, error)
                return redirect("datechange:detail", pk=pk)
            # Same record, new dates: days, credits taken and CTO credit held stay as they are.
            cover = cover_for(target) if change.kind == DC.KIND_LEAVE else None
            if cover is not None:  # Part 3: the CTO days move with the leave, same split, same credit held
                cover.start_date, cover.end_date = shifted_cover(target, cover, change.new_start_date)
                cover.save(update_fields=["start_date", "end_date", "updated_at"])
            target.start_date, target.end_date = change.new_start_date, change.new_end_date
            target.save(update_fields=["start_date", "end_date", "updated_at"])
        change.status = new_status
        change.save(update_fields=["status", "updated_at"])
        DateChangeAction.objects.create(request=change, action=action, resulting_status=new_status,
                                        notes=notes, acted_by=request.user)
    notify_change(change)
    messages.success(request, "New dates are now in force." if new_status == DC.APPLIED else "Action recorded.")
    return redirect("datechange:detail", pk=pk)
