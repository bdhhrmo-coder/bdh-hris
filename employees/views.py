from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from .forms import EmployeeForm
from .models import Employee, EmployeeEditHistory
from .permissions import (
    can_open_employee_edit_screen,
    get_acting_employee,
    is_self_record_locked,
)


@login_required
def employee_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    acting_employee = get_acting_employee(request.user)
    can_edit = can_open_employee_edit_screen(acting_employee) and not (
        acting_employee and is_self_record_locked(acting_employee, employee)
    )
    self_locked = bool(
        acting_employee and is_self_record_locked(acting_employee, employee)
    )
    return render(
        request,
        "employees/employee_detail.html",
        {
            "employee": employee,
            "can_edit": can_edit,
            "self_locked": self_locked,
            "history": employee.edit_history.select_related("changed_by")[:50],
        },
    )


@login_required
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    acting_employee = get_acting_employee(request.user)

    if not can_open_employee_edit_screen(acting_employee):
        raise PermissionDenied("You are not authorized to edit employee records.")

    if is_self_record_locked(acting_employee, employee):
        messages.info(
            request,
            "This is your own employee record. It is read-only to you — "
            "ask another HR Administrator to make this change.",
        )
        return redirect("employees:employee_detail", pk=employee.pk)

    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee, acting_employee=acting_employee)
        if form.is_valid():
            changes = form.changed_editable_data()
            if changes and not form.cleaned_data.get("reason", "").strip():
                form.add_error("reason", "A reason is required when changing employee data.")
            else:
                employee = form.save()
                for field_name, (old_value, new_value) in changes.items():
                    EmployeeEditHistory.objects.create(
                        employee=employee,
                        field_name=field_name,
                        field_label=form.fields[field_name].label or field_name,
                        old_value=old_value if old_value is not None else "",
                        new_value=new_value if new_value is not None else "",
                        reason=form.cleaned_data["reason"].strip(),
                        changed_by=request.user,
                    )
                if changes:
                    messages.success(
                        request, f"Saved {len(changes)} change(s) with reason logged."
                    )
                else:
                    messages.info(request, "No changes were made.")
                return redirect("employees:employee_detail", pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee, acting_employee=acting_employee)

    return render(request, "employees/employee_form.html", {"form": form, "employee": employee})
