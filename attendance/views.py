from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from printouts.http import pdf_response

from .deductions import sync_undertime_deduction
from .forms import BiometricImportForm, FormalCorrectionForm, MinorCorrectionForm
from .importer import import_biometric_file
from .models import AttendanceCorrectionRequest, AttendanceCorrectionRequestAction, AttendanceRecord, BiometricColumnMapping
from .notifications import notify_status_change
from .permissions import (
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
    corrections = acting_employee.attendance_correction_requests.all()
    return render(request, "attendance/my_attendance.html", {"records": records, "corrections": corrections})


@login_required
def formal_correction_apply(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    if request.method == "POST":
        form = FormalCorrectionForm(request.POST)
        if form.is_valid():
            correction = form.save(commit=False)
            correction.employee = acting_employee
            correction.correction_type = AttendanceCorrectionRequest.FORMAL
            correction.filed_by = request.user
            correction.full_clean()
            correction.save()
            AttendanceCorrectionRequestAction.objects.create(
                request=correction, action="submit", resulting_status=correction.status, acted_by=request.user,
            )
            notify_status_change(correction)
            messages.success(request, "Attendance correction request submitted.")
            return redirect("attendance:my_attendance")
    else:
        form = FormalCorrectionForm()

    return render(request, "attendance/formal_correction_apply.html", {
        "form": form, "ictu_reasons": AttendanceCorrectionRequest.ICTU_VALIDATED_REASONS,
    })


def _finalize_correction(correction, actor_user):
    """Single point of commitment: only here does the correction actually
    write to AttendanceRecord and sync the undertime->VL deduction."""
    record, _ = AttendanceRecord.objects.get_or_create(
        employee=correction.employee, date=correction.date,
        defaults={"recorded_by": actor_user},
    )
    record.time_in = correction.requested_time_in
    record.time_out = correction.requested_time_out
    record.is_absent = correction.requested_is_absent
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
        form = MinorCorrectionForm(request.POST)
        if form.is_valid():
            correction = form.save(commit=False)
            correction.correction_type = AttendanceCorrectionRequest.MINOR
            correction.filed_by = request.user
            correction.full_clean()
            correction.save()
            AttendanceCorrectionRequestAction.objects.create(
                request=correction, action="submit", resulting_status=correction.status, acted_by=request.user,
            )
            if is_hr_administrator(acting_employee):
                # HR Administrator filing it themselves IS the authorization
                # (§3) — no point routing it back to the same role. Only
                # notify once, for the final APPROVED status — the
                # momentary SUBMITTED status in between never needs anyone
                # notified about it.
                _finalize_correction(correction, request.user)
                correction.status = AttendanceCorrectionRequest.APPROVED
                correction.save(update_fields=["status", "updated_at"])
                AttendanceCorrectionRequestAction.objects.create(
                    request=correction, action="approve", resulting_status=correction.status, acted_by=request.user,
                    notes="Self-authorized by HR Administrator.",
                )
                notify_status_change(correction)
                messages.success(request, "Correction recorded and authorized.")
            else:
                notify_status_change(correction)
                messages.success(request, "Correction filed. Awaiting HR Administrator authorization.")
            return redirect("attendance:correction_queue")
    else:
        form = MinorCorrectionForm()

    return render(request, "attendance/minor_correction_create.html", {"form": form})


@login_required
def correction_queue(request):
    acting_employee = get_acting_employee(request.user)
    requests = visible_correction_requests_for(acting_employee).select_related("employee").order_by("submitted_at", "pk")  # oldest first: first filed, first acted on (owner, 2026-10-06)
    return render(request, "attendance/correction_queue.html", {"requests": requests})


@login_required
def correction_action(request, pk):
    """Single POST endpoint for every routing step. Which transitions are
    legal depends on correction_type AND status AND the acting employee's
    role — mirrors leave.views.leave_action's dual-path branching."""
    acting_employee = get_acting_employee(request.user)
    correction = get_object_or_404(AttendanceCorrectionRequest, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()

    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    status = correction.status

    def apply_transition(new_status, action_name):
        correction.status = new_status
        correction.save(update_fields=["status", "updated_at"])
        AttendanceCorrectionRequestAction.objects.create(
            request=correction, action=action_name, resulting_status=new_status, notes=notes, acted_by=request.user,
        )
        notify_status_change(correction)

    allowed = False

    if correction.correction_type == AttendanceCorrectionRequest.MINOR:
        if status == AttendanceCorrectionRequest.SUBMITTED and is_hr_administrator(acting_employee):
            if action == "approve":
                _finalize_correction(correction, request.user)
                allowed = True
                apply_transition(AttendanceCorrectionRequest.APPROVED, "approve")
            elif action == "reject":
                allowed = True
                apply_transition(AttendanceCorrectionRequest.REJECTED, "reject")
    else:
        stop = lambda: apply_transition(  # noqa: E731
            AttendanceCorrectionRequest.REJECTED if action == "reject" else AttendanceCorrectionRequest.RETURNED,
            action,
        )
        if status == AttendanceCorrectionRequest.SUBMITTED and is_validator_for(acting_employee, correction):
            # ICTU Staff (Offline / Failed Attempt / Wrong Button) or HR
            # (Attended activity / Others) - BDH-ADM-AO-01F10.
            if action == "validate":
                allowed = True
                apply_transition(AttendanceCorrectionRequest.VALIDATED, "validate")
            elif action in ("reject", "return"):
                allowed = True
                stop()
        elif status == AttendanceCorrectionRequest.VALIDATED and is_administrative_officer(acting_employee):
            if action == "approve":
                _finalize_correction(correction, request.user)
                allowed = True
                apply_transition(AttendanceCorrectionRequest.APPROVED, "approve")
            elif action in ("reject", "return"):
                allowed = True
                stop()
        # Old Supervisor -> HR -> AO chain, only for requests already endorsed
        # before 2026-10-06 (nothing new reaches these statuses).
        elif status == AttendanceCorrectionRequest.ENDORSED_BY_SUPERVISOR and is_hr(acting_employee):
            if action == "process":
                allowed = True
                apply_transition(AttendanceCorrectionRequest.PROCESSED_BY_HR, "process")
            elif action in ("reject", "return"):
                allowed = True
                apply_transition(
                    AttendanceCorrectionRequest.REJECTED if action == "reject" else AttendanceCorrectionRequest.RETURNED,
                    action,
                )
        elif status == AttendanceCorrectionRequest.PROCESSED_BY_HR and is_administrative_officer(acting_employee):
            if action == "approve":
                _finalize_correction(correction, request.user)
                allowed = True
                apply_transition(AttendanceCorrectionRequest.APPROVED, "approve")
            elif action in ("reject", "return"):
                allowed = True
                apply_transition(
                    AttendanceCorrectionRequest.REJECTED if action == "reject" else AttendanceCorrectionRequest.RETURNED,
                    action,
                )

    if allowed:
        messages.success(request, "Action recorded.")
    else:
        raise PermissionDenied("You are not authorized to take this action on this request.")

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
