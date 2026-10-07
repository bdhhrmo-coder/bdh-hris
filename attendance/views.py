from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from printouts.http import pdf_response

from .deductions import sync_undertime_deduction
from .forms import ActionForm, BiometricImportForm, MinorEmployeeForm, formset_rows, line_formset, save_lines
from .importer import import_biometric_file
from .models import AttendanceCorrectionRequest, AttendanceCorrectionRequestAction, AttendanceRecord, BiometricColumnMapping
from .notifications import notify_status_change
from .permissions import (
    available_actions,
    can_view_correction_request,
    get_acting_employee,
    is_administrative_officer,
    is_hr,
    is_hr_administrator,
    is_hr_processor,
    is_validator_for,
    visible_correction_requests_for,
)


@login_required
def biometric_import(request):
    acting_employee = get_acting_employee(request.user)
    if not is_hr(acting_employee):
        raise PermissionDenied("Only HR may import biometric attendance data.")

    mapping = BiometricColumnMapping.get_active()
    batch = None
    if request.method == "POST":
        form = BiometricImportForm(request.POST, request.FILES)
        if form.is_valid():
            if mapping is None:
                messages.error(request, "No active biometric column mapping is configured. Contact HR Admin.")
            else:
                batch = import_biometric_file(form.cleaned_data["file"].file, form.cleaned_data["file"].name, mapping, request.user)
                messages.success(
                    request,
                    f"Imported {batch.imported_count} record(s), skipped {batch.skipped_count} "
                    f"(already manually corrected), {batch.error_count} error(s).",
                )
    else:
        form = BiometricImportForm()

    return render(request, "attendance/biometric_import.html", {"form": form, "mapping": mapping, "batch": batch})


@login_required
def my_attendance(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    records = acting_employee.attendance_records.all()
    corrections = acting_employee.attendance_correction_requests.prefetch_related("lines")
    # The correction section starts closed, unless one was returned and needs the employee's action.
    has_returned = corrections.filter(status=AttendanceCorrectionRequest.RETURNED).exists()
    return render(request, "attendance/my_attendance.html",
                  {"records": records, "corrections": corrections, "has_returned": has_returned})


def _line_editor_context(formset, *, formal):
    return {
        "formset": formset,
        "rows": formset_rows(formset),
        "formal": formal,
        "max_lines": AttendanceCorrectionRequest.MAX_LINES,
        "reason_choices": AttendanceCorrectionRequest.REASON_CATEGORY_CHOICES,
        "ictu_reasons": sorted(AttendanceCorrectionRequest.ICTU_VALIDATED_REASONS),
        "detail_reasons": sorted(AttendanceCorrectionRequest.REASONS_NEEDING_DETAILS),
    }


def _log(correction, action, user, notes=""):
    AttendanceCorrectionRequestAction.objects.create(
        request=correction, action=action, resulting_status=correction.status, notes=notes, acted_by=user,
    )


@login_required
def formal_correction_apply(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    formset = line_formset(request.POST if request.method == "POST" else None, formal=True, employee=acting_employee)
    if request.method == "POST" and formset.is_valid():
        with transaction.atomic():
            correction = AttendanceCorrectionRequest.objects.create(
                employee=acting_employee, correction_type=AttendanceCorrectionRequest.FORMAL, filed_by=request.user,
            )
            save_lines(correction, formset)
            _log(correction, "submit", request.user)
        notify_status_change(correction)
        messages.success(request, "Attendance correction request submitted.")
        return redirect("attendance:my_attendance")

    return render(request, "attendance/formal_correction_apply.html", _line_editor_context(formset, formal=True))


def _finalize_correction(correction, actor_user):
    """Single point of commitment: only here does the correction actually
    write to AttendanceRecord (one per line) and sync the undertime->VL
    deduction."""
    for line in correction.lines.all():
        record, _ = AttendanceRecord.objects.get_or_create(
            employee=correction.employee, date=line.date, defaults={"recorded_by": actor_user},
        )
        record.time_in = None if line.is_absent else line.time_in
        record.time_out = None if line.is_absent else line.time_out
        record.is_absent = line.is_absent
        record.source = AttendanceRecord.SOURCE_MANUAL
        record.recorded_by = actor_user
        record.notes = f"Corrected via {correction.get_correction_type_display()} request #{correction.pk}."
        record.save()
        sync_undertime_deduction(record, actor_user)


@login_required
def minor_correction_create(request):
    acting_employee = get_acting_employee(request.user)
    if not is_hr_processor(acting_employee) and not is_hr_administrator(acting_employee):
        raise PermissionDenied("Only HR may file a minor administrative attendance correction.")

    if request.method == "POST":
        emp_form = MinorEmployeeForm(request.POST)
        employee = emp_form.cleaned_data["employee"] if emp_form.is_valid() else None
        formset = line_formset(request.POST, formal=False, employee=employee)
        if employee is not None and formset.is_valid():
            with transaction.atomic():
                correction = AttendanceCorrectionRequest.objects.create(
                    employee=employee, correction_type=AttendanceCorrectionRequest.MINOR, filed_by=request.user,
                )
                save_lines(correction, formset)
                _log(correction, "submit", request.user)
                if is_hr_administrator(acting_employee):
                    # HR Administrator filing it themselves IS the
                    # authorization (§3) - no point routing it back to the
                    # same role. Only the final APPROVED status is notified.
                    _finalize_correction(correction, request.user)
                    correction.status = AttendanceCorrectionRequest.APPROVED
                    correction.save(update_fields=["status", "updated_at"])
                    _log(correction, "approve", request.user, "Self-authorized by HR Administrator.")
            notify_status_change(correction)
            if correction.status == AttendanceCorrectionRequest.APPROVED:
                messages.success(request, "Correction recorded and authorized.")
            else:
                messages.success(request, "Correction filed. Awaiting HR Administrator authorization.")
            return redirect("attendance:correction_queue")
    else:
        emp_form = MinorEmployeeForm()
        formset = line_formset(None, formal=False)

    return render(request, "attendance/minor_correction_create.html",
                  {"emp_form": emp_form, **_line_editor_context(formset, formal=False)})


def _is_filer(acting_employee, user, correction):
    if correction.correction_type == AttendanceCorrectionRequest.FORMAL:
        return acting_employee is not None and acting_employee.pk == correction.employee_id
    return correction.filed_by_id == user.pk


@login_required
def correction_edit(request, pk):
    """The filer fixes a RETURNED request and sends it again. It starts
    over from the first approval step (owner decision 2026-10-07); the
    return and its remark stay in the history."""
    acting_employee = get_acting_employee(request.user)
    correction = get_object_or_404(AttendanceCorrectionRequest, pk=pk)
    if not _is_filer(acting_employee, request.user, correction):
        raise PermissionDenied("Only the person who filed this request can edit it.")
    if correction.status != AttendanceCorrectionRequest.RETURNED:
        messages.error(request, "Only a returned request can be edited.")
        return redirect("attendance:correction_detail", pk=pk)

    formal = correction.correction_type == AttendanceCorrectionRequest.FORMAL
    formset = line_formset(request.POST if request.method == "POST" else None, formal=formal,
                           employee=correction.employee, request_obj=correction)
    if request.method == "POST" and formset.is_valid():
        with transaction.atomic():
            save_lines(correction, formset)
            correction.status = AttendanceCorrectionRequest.SUBMITTED
            correction.save(update_fields=["status", "updated_at"])
            _log(correction, "resubmit", request.user, request.POST.get("resubmit_note", "").strip()[:255])
        notify_status_change(correction)
        messages.success(request, "Request resubmitted. It starts again from the first approval step.")
        return redirect("attendance:correction_detail", pk=pk)

    return render(request, "attendance/correction_edit.html", {
        "correction": correction, "latest_return": correction.latest_return(),
        **_line_editor_context(formset, formal=formal),
    })


@login_required
def correction_queue(request):
    acting_employee = get_acting_employee(request.user)
    requests = (visible_correction_requests_for(acting_employee).select_related("employee")
                .prefetch_related("lines").order_by("submitted_at", "pk"))  # oldest first (owner, 2026-10-06)
    return render(request, "attendance/correction_queue.html", {"requests": requests})


@login_required
def correction_detail(request, pk):
    """Lines, return history and - for whoever acts next - the
    Approve/Validate/Return buttons (whole request only)."""
    acting_employee = get_acting_employee(request.user)
    correction = get_object_or_404(AttendanceCorrectionRequest.objects.select_related("employee"), pk=pk)
    if not (can_view_correction_request(acting_employee, correction) or _is_filer(acting_employee, request.user, correction)):
        raise PermissionDenied("You are not authorized to view this correction request.")
    return render(request, "attendance/correction_detail.html", {
        "correction": correction,
        "lines": correction.sorted_lines,
        "history": correction.actions.select_related("acted_by__employee").order_by("acted_at", "pk"),
        "actions": available_actions(acting_employee, correction),
        "can_edit": correction.is_returned and _is_filer(acting_employee, request.user, correction),
        "action_form": ActionForm(),
    })


_NEXT_STATUS = {
    "validate": AttendanceCorrectionRequest.VALIDATED,
    "process": AttendanceCorrectionRequest.PROCESSED_BY_HR,
    "approve": AttendanceCorrectionRequest.APPROVED,
    "return": AttendanceCorrectionRequest.RETURNED,
    "reject": AttendanceCorrectionRequest.REJECTED,
}


@login_required
def correction_action(request, pk):
    """Single POST endpoint for every routing step. The whole request is
    acted on - there is no line-by-line approval (Batch 2, Item 6)."""
    if request.method != "POST":
        return redirect("attendance:correction_detail", pk=pk)
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    correction = get_object_or_404(AttendanceCorrectionRequest, pk=pk)
    form = ActionForm(request.POST)
    action = request.POST.get("action")
    if action not in {code for code, _ in available_actions(acting_employee, correction)}:
        raise PermissionDenied("You are not authorized to take this action on this request.")
    if not form.is_valid():
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
        return redirect("attendance:correction_detail", pk=pk)

    with transaction.atomic():
        if action == "approve":
            _finalize_correction(correction, request.user)
        correction.status = _NEXT_STATUS[action]
        correction.save(update_fields=["status", "updated_at"])
        _log(correction, action, request.user, form.cleaned_data["notes"].strip())
    notify_status_change(correction)
    messages.success(request, "Action recorded.")
    return redirect("attendance:correction_queue")


@login_required
def print_correction_form(request, pk):
    """Missed Log Justification Form, BDH-ADM-AO-01F10 Rev. 2 (see
    attendance/correction_form.py). FORMAL corrections only - MINOR ones
    are HR's internal record fixes and have no employee form."""
    from .correction_form import render_pdf

    acting_employee = get_acting_employee(request.user)
    correction = get_object_or_404(AttendanceCorrectionRequest, pk=pk, correction_type=AttendanceCorrectionRequest.FORMAL)
    if not can_view_correction_request(acting_employee, correction):
        raise PermissionDenied("You are not authorized to view this correction request.")
    return pdf_response(
        request, lambda: render_pdf(correction, printed_by=request.user),
        f"Missed-Log-Justification_{correction.employee.surname}_{correction.pk}.pdf", "attendance:my_attendance",
    )
