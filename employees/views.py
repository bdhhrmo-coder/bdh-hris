from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render

from orgstructure.models import Section

from accounts.models import RoleAssignment

from .forms import EmployeeCreateForm, EmployeeForm
from .models import EducationHistory, Employee, EmployeeEditHistory
from .permissions import (
    can_create_employee,
    can_list_employees,
    can_open_employee_edit_screen,
    can_view_employee,
    get_acting_employee,
    is_self_record_locked,
)

EducationHistoryFormSet = inlineformset_factory(
    Employee,
    EducationHistory,
    fields=["education_level", "school", "degree_course", "units_earned"],
    extra=1,
    can_delete=True,
)


def _lock_education_formset(formset):
    """
    Education history is part of HR master data, not account/login data —
    so, like the main form's HR fields, only an HR Administrator may edit it
    (a System Administrator visiting this screen only for employee_id/
    is_active should not be able to add/remove education rows).
    `disabled=True` makes Django ignore submitted data for these fields, the
    same defense used in EmployeeForm.
    """
    for edu_form in formset.forms:
        for field in edu_form.fields.values():
            field.disabled = True


@login_required
def employee_list(request):
    acting_employee = get_acting_employee(request.user)
    if not can_list_employees(acting_employee):
        raise PermissionDenied("You are not authorized to browse employee records.")

    employees = Employee.objects.select_related(None).prefetch_related("sections")

    query = request.GET.get("q", "").strip()
    if query:
        employees = employees.filter(
            Q(surname__icontains=query)
            | Q(first_name__icontains=query)
            | Q(employee_id__icontains=query)
        )

    section_id = request.GET.get("section")
    if section_id:
        employees = employees.filter(sections__id=section_id)

    employment_status = request.GET.get("employment_status")
    if employment_status:
        employees = employees.filter(employment_status=employment_status)

    return render(
        request,
        "employees/employee_list.html",
        {
            "employees": employees.distinct(),
            "sections": Section.objects.filter(is_active=True),
            "query": query,
            "selected_section": section_id or "",
            "selected_status": employment_status or "",
            "can_create": can_create_employee(acting_employee),
        },
    )


@login_required
def employee_create(request):
    acting_employee = get_acting_employee(request.user)
    if not can_create_employee(acting_employee):
        raise PermissionDenied(
            "Only a System Administrator can create a new employee record."
        )

    if request.method == "POST":
        form = EmployeeCreateForm(request.POST)
        if form.is_valid():
            employee = form.save()
            messages.success(
                request,
                "Employee record created. An HR Administrator can now fill in "
                "the rest of the master data.",
            )
            return redirect("employees:employee_detail", pk=employee.pk)
    else:
        form = EmployeeCreateForm()

    return render(request, "employees/employee_create_form.html", {"form": form})


@login_required
def employee_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    acting_employee = get_acting_employee(request.user)

    if not can_view_employee(acting_employee, employee):
        raise PermissionDenied("You are not authorized to view this employee record.")

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
            "education_history": employee.education_history.all(),
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

    is_hr_admin = bool(
        acting_employee and acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)
    )

    if request.method == "POST":
        form = EmployeeForm(request.POST, instance=employee, acting_employee=acting_employee)
        education_formset = EducationHistoryFormSet(request.POST, instance=employee)
        if not is_hr_admin:
            _lock_education_formset(education_formset)
        if form.is_valid() and education_formset.is_valid():
            changes = form.changed_editable_data()
            if changes and not form.cleaned_data.get("reason", "").strip():
                form.add_error("reason", "A reason is required when changing employee data.")
            else:
                employee = form.save()
                education_formset.save()
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
        education_formset = EducationHistoryFormSet(instance=employee)
        if not is_hr_admin:
            _lock_education_formset(education_formset)

    return render(
        request,
        "employees/employee_form.html",
        {"form": form, "employee": employee, "education_formset": education_formset},
    )
