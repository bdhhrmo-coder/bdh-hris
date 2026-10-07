from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render

from orgstructure.models import Section

from accounts.models import RoleAssignment

from django.utils import timezone

from .forms import EmployeeCreateForm, EmployeeForm, SelfServiceProfileForm
from . import archive as archive_rules
from .models import EducationHistory, Employee, EmployeeEditHistory, EmployeeProfileEditRequest
from .permissions import (
    can_create_employee,
    can_list_employees,
    can_open_employee_edit_screen,
    can_review_profile_requests,
    can_review_this_request,
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
    show_archived = request.GET.get("archived") == "1"
    if not show_archived:
        employees = employees.filter(archived_at__isnull=True)

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
            "show_archived": show_archived,
            "undo": _pop_undo(request),
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
            "can_archive": archive_rules.can_archive(acting_employee, employee),
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


@login_required
def my_profile_edit_request(request):
    """
    Self-service edit for the employee's own contact info / address / civil
    status (CLAUDE.md §6.4). This never saves the Employee record directly —
    it only ever creates PENDING EmployeeProfileEditRequest rows for an HR
    Administrator to approve.
    """
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    if request.method == "POST":
        form = SelfServiceProfileForm(request.POST, instance=acting_employee)
        if form.is_valid():
            changes = form.requested_changes()
            for field_name, (old_value, new_value) in changes.items():
                # Superseded by this new submission — drop any earlier
                # pending request for the same field rather than stacking up.
                EmployeeProfileEditRequest.objects.filter(
                    employee=acting_employee, field_name=field_name, status=EmployeeProfileEditRequest.PENDING
                ).delete()
                EmployeeProfileEditRequest.objects.create(
                    employee=acting_employee,
                    field_name=field_name,
                    old_value=old_value,
                    requested_value=new_value,
                )
            if changes:
                messages.success(
                    request,
                    f"Submitted {len(changes)} change(s) for HR Administrator approval. "
                    "They won't take effect until approved.",
                )
            else:
                messages.info(request, "No changes were made.")
            return redirect("employees:my_profile_edit_request")
    else:
        form = SelfServiceProfileForm(instance=acting_employee)

    pending = acting_employee.profile_edit_requests.filter(status=EmployeeProfileEditRequest.PENDING)
    recent = acting_employee.profile_edit_requests.exclude(status=EmployeeProfileEditRequest.PENDING)[:20]
    return render(
        request,
        "employees/my_profile_edit_request.html",
        {"form": form, "pending": pending, "recent": recent},
    )


@login_required
def profile_edit_request_queue(request):
    acting_employee = get_acting_employee(request.user)
    if not can_review_profile_requests(acting_employee):
        raise PermissionDenied("Only an HR Administrator can review self-service edit requests.")

    requests_qs = EmployeeProfileEditRequest.objects.filter(
        status=EmployeeProfileEditRequest.PENDING
    ).select_related("employee").order_by("requested_at", "pk")  # oldest first: first filed, first acted on (owner, 2026-10-06)
    return render(
        request,
        "employees/profile_edit_request_queue.html",
        {"requests": requests_qs, "acting_employee": acting_employee},
    )


@login_required
def profile_edit_request_review(request, pk):
    acting_employee = get_acting_employee(request.user)
    edit_request = get_object_or_404(EmployeeProfileEditRequest, pk=pk)

    if not can_review_profile_requests(acting_employee):
        raise PermissionDenied("Only an HR Administrator can review self-service edit requests.")

    if not can_review_this_request(acting_employee, edit_request):
        messages.error(
            request, "You cannot review your own self-service edit request — "
            "ask another HR Administrator."
        )
        return redirect("employees:profile_edit_request_queue")

    if edit_request.status != EmployeeProfileEditRequest.PENDING:
        messages.info(request, "This request has already been reviewed.")
        return redirect("employees:profile_edit_request_queue")

    decision = request.POST.get("decision")
    if decision == "approve":
        target = edit_request.employee
        current_value = getattr(target, edit_request.field_name)
        setattr(target, edit_request.field_name, edit_request.requested_value)
        target.save(update_fields=[edit_request.field_name])
        EmployeeEditHistory.objects.create(
            employee=target,
            field_name=edit_request.field_name,
            field_label=dict(EmployeeProfileEditRequest.SELF_SERVICE_FIELDS).get(
                edit_request.field_name, edit_request.field_name
            ),
            old_value=current_value or "",
            new_value=edit_request.requested_value,
            reason="Self-service change request approved.",
            changed_by=request.user,
        )
        edit_request.status = EmployeeProfileEditRequest.APPROVED
        edit_request.reviewed_by = request.user
        edit_request.reviewed_at = timezone.now()
        edit_request.save()
        messages.success(request, "Change approved and applied.")
    elif decision == "reject":
        edit_request.status = EmployeeProfileEditRequest.REJECTED
        edit_request.reviewed_by = request.user
        edit_request.reviewed_at = timezone.now()
        edit_request.review_notes = request.POST.get("review_notes", "").strip()
        edit_request.save()
        messages.info(request, "Change rejected.")
    else:
        messages.error(request, "Unknown decision.")

    return redirect("employees:profile_edit_request_queue")


# -- archive / undo / restore (Batch 3 Item 5) ---------------------------------

def _pop_undo(request):
    """The 8-second Undo offer, shown once on the page after archiving."""
    from django.utils import timezone

    offer = request.session.pop("undo_archive", None)
    if not offer:
        return None
    left = offer["until"] - timezone.now().timestamp()
    return {**offer, "seconds": round(left)} if left > 1 else None


@login_required
def employee_archive(request, pk):
    from django.utils import timezone

    employee = get_object_or_404(Employee, pk=pk)
    acting_employee = get_acting_employee(request.user)
    if request.method != "POST":
        return redirect("employees:employee_detail", pk=pk)
    if not archive_rules.can_archive(acting_employee, employee):
        raise PermissionDenied("Only an HR Administrator may archive an employee record (not their own).")
    if employee.archived_at:
        messages.info(request, "This record is already archived.")
        return redirect("employees:employee_detail", pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Give the reason for archiving this record.")
        return redirect("employees:employee_detail", pk=pk)
    archive_rules.archive(employee, request.user, reason)
    # No toast here: the Undo snackbar is the message (no double messages).
    request.session["undo_archive"] = {"pk": employee.pk, "name": employee.full_name,
                                       "until": timezone.now().timestamp() + archive_rules.UNDO_SECONDS}
    return redirect("employees:employee_list")


@login_required
def employee_restore(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    acting_employee = get_acting_employee(request.user)
    if request.method != "POST":
        return redirect("employees:employee_detail", pk=pk)
    if not archive_rules.can_archive(acting_employee, employee):
        raise PermissionDenied("Only an HR Administrator may restore an archived employee record.")
    if not employee.archived_at:
        messages.info(request, "This record is not archived.")
        return redirect("employees:employee_detail", pk=pk)
    undo = request.POST.get("undo") == "1"
    archive_rules.restore(employee, request.user, undo=undo)
    messages.success(request, f"{'Archive undone' if undo else 'Record restored'}: {employee.full_name}.")
    return redirect("employees:employee_list" if undo else "employees:employee_detail", **({} if undo else {"pk": pk}))
