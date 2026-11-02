from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from documents.requirements import requirement_status_for
from employees.models import Employee

from printouts.http import pdf_response

from . import routing
from .balances import (
    compute_available_cto_balance,
    exceeds_monthly_cap,
    filing_deadline_for,
    forms_illegal_consecutive_run,
    plan_allocation,
)
from .claims import (
    add_line,
    approve_claim,
    claimable_ot_lines,
    correct_day_type,
    create_exception_claim,
    submission_errors,
    submit_claim,
)
from .forms import (
    ClaimStartForm,
    CTOUsageApplicationForm,
    ExceptionClaimForm,
    ManualClaimLineForm,
    OpeningLineForm,
    SeparationForm,
    OTClaimLineForm,
    OvertimeLineFormSet,
    OvertimeRequestForm,
)
from .models import (
    DAY_TYPE_CHOICES,
    CTOCarryOverReason,
    CTOClaim,
    CTOClaimLine,
    CTOCreditAllocation,
    CTOUsageApplication,
    CTOUsageApplicationAction,
    OvertimeRequest,
    RoutedRecord,
)
from .notifications import notify_status_change
from .opening import add_opening_line, can_enter_opening, entry_is_open, opening_entry_actors, void_opening_line
from .overtime import file_ot, filing_capacities
from . import carryover
from .separation import can_set_separation, is_separating, set_separation
from .permissions import (
    UNIT_STEP,
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    can_endorse,
    can_endorse_second,
    can_view_cto_application,
    supervisor_step_result,
    get_acting_employee,
    is_administrative_officer,
    is_chief_of_hospital,
    is_cto_eligible,
    is_hr,
    is_supervisor_of,
    visible_cto_applications_for,
)


def _employee_or_403(request):
    employee = get_acting_employee(request.user)
    if employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    return employee


def _show_errors(request, exc):
    for message in exc.messages:
        messages.error(request, message)


def _can_view_record(employee, record, owners):
    return (
        employee.pk in owners
        # viewing=True: an approver who handed the area to an OIC keeps view-only access (2026-10-13)
        or is_hr(employee, True) or is_administrative_officer(employee, True) or is_chief_of_hospital(employee, True)
        or routing.can_act(employee, record)
        or any(is_supervisor_of(employee, o, viewing=True) for o in record_people(record))
    )


def record_people(record):
    if isinstance(record, OvertimeRequest):
        return [line.employee for line in record.lines.select_related("employee")]
    return [record.employee]


# --- OT requests ---------------------------------------------------------

def _ot_form_view(request, ot=None):
    filer = _employee_or_403(request)
    capacities = filing_capacities(filer)
    if not capacities:
        raise PermissionDenied("Only a Supervisor, HR or the AO can file OT.")
    initial_lines = []
    initial = {}
    if ot is not None:
        initial = {"filed_as": ot.filed_as, "purpose": ot.purpose, "past_date_reason": ot.past_date_reason}
        initial_lines = [
            {"employee": l.employee, "work_date": l.work_date, "approved_hours": l.approved_hours, "day_type": l.day_type}
            for l in ot.lines.all()
        ]
    if request.method == "POST":
        form = OvertimeRequestForm(request.POST, capacities=capacities)
        formset = OvertimeLineFormSet(request.POST, prefix="lines")
        if form.is_valid() and formset.is_valid():
            lines = [f.cleaned_data for f in formset if f.cleaned_data]
            try:
                ot, warnings = file_ot(
                    filer, form.cleaned_data["filed_as"], form.cleaned_data["purpose"],
                    form.cleaned_data["past_date_reason"], lines, request.user, timezone.localdate(), ot=ot,
                )
            except ValidationError as exc:
                _show_errors(request, exc)
            else:
                for w in warnings:
                    messages.warning(request, w)
                messages.success(request, "OT request submitted.")
                return redirect(ot.get_absolute_url())
    else:
        form = OvertimeRequestForm(initial=initial, capacities=capacities)
        formset = OvertimeLineFormSet(initial=initial_lines, prefix="lines")
    return render(request, "cto/ot_form.html", {"form": form, "formset": formset, "ot": ot})


@login_required
def ot_new(request):
    return _ot_form_view(request)


@login_required
def ot_edit(request, pk):
    ot = get_object_or_404(OvertimeRequest, pk=pk, status=RoutedRecord.RETURNED)
    if ot.filed_by_id != _employee_or_403(request).pk:
        raise PermissionDenied("Only the filer can edit a returned OT request.")
    return _ot_form_view(request, ot)


@login_required
def ot_detail(request, pk):
    employee = _employee_or_403(request)
    ot = get_object_or_404(OvertimeRequest, pk=pk)
    if not _can_view_record(employee, ot, {ot.filed_by_id}):
        raise PermissionDenied("You are not authorized to view this OT request.")
    return render(request, "cto/ot_detail.html", {
        "ot": ot,
        "lines": ot.lines.select_related("employee"),
        "can_act": routing.can_act(employee, ot),
        "is_filer": employee.pk == ot.filed_by_id,
        "step_label": routing.STEP_LABELS.get(ot.current_step, ""),
    })


@login_required
def ot_action(request, pk):
    if request.method != "POST":
        return redirect("cto:ot_detail", pk=pk)
    employee = _employee_or_403(request)
    ot = get_object_or_404(OvertimeRequest, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "")
    try:
        with transaction.atomic():
            if action == "approve":
                routing.approve_step(ot, employee, request.user, notes)
            elif action == "return":
                routing.return_record(ot, employee, request.user, notes)
            elif action == "cancel" and ot.filed_by_id == employee.pk and ot.status == RoutedRecord.RETURNED:
                ot.status = RoutedRecord.CANCELLED
                ot.save(update_fields=["status", "updated_at"])
                ot.actions.create(action="cancel", acted_by=request.user)
            else:
                raise PermissionDenied("You cannot take this action.")
    except ValidationError as exc:
        _show_errors(request, exc)
    else:
        messages.success(request, "Action recorded.")
    return redirect("cto:ot_detail", pk=pk)


# --- CTO claims ----------------------------------------------------------

@login_required
def claim_new(request):
    employee = _employee_or_403(request)
    if not is_cto_eligible(employee):
        raise PermissionDenied("CTO is not applicable to the Chief of Hospital.")
    form = ClaimStartForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        claim = CTOClaim.objects.create(employee=employee, duty_type=form.cleaned_data["duty_type"], filed_by=request.user)
        return redirect(claim.get_absolute_url())
    return render(request, "cto/claim_new.html", {"form": form, "title": "New CTO claim"})


@login_required
def exception_claim_new(request):
    employee = _employee_or_403(request)
    if not employee.is_hr_administrator():
        raise PermissionDenied("Only an HR Administrator may file an exception CTO claim.")
    form = ExceptionClaimForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            claim = create_exception_claim(
                employee, form.cleaned_data["employee"], form.cleaned_data["duty_type"],
                form.cleaned_data["exception_reason"], request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect(claim.get_absolute_url())
    return render(request, "cto/claim_new.html", {"form": form, "title": "Exception CTO claim (no OT request)"})


def _can_edit_claim(employee, claim):
    if claim.status not in RoutedRecord.EDITABLE_STATUSES:
        return False
    if claim.is_exception():
        return claim.filed_by_id == employee.user_id
    return claim.employee_id == employee.pk


@login_required
def claim_detail(request, pk):
    employee = _employee_or_403(request)
    claim = get_object_or_404(CTOClaim.objects.select_related("employee"), pk=pk)
    if not _can_view_record(employee, claim, {claim.employee_id}) and claim.filed_by_id != request.user.pk:
        raise PermissionDenied("You are not authorized to view this CTO claim.")
    today = timezone.localdate()
    editable = _can_edit_claim(employee, claim)
    can_act = routing.can_act(employee, claim)
    line_form = None
    if editable:
        if claim.uses_ot_lines():
            line_form = OTClaimLineForm(ot_lines=claimable_ot_lines(claim.employee))
        else:
            line_form = ManualClaimLineForm()
    lines = list(claim.lines.select_related("ot_line"))
    for line in lines:
        line.filing_deadline = filing_deadline_for(line.work_date)
    return render(request, "cto/claim_detail.html", {
        "claim": claim,
        "lines": lines,
        "line_form": line_form,
        "editable": editable,
        "submission_errors": submission_errors(claim, today) if editable else [],
        "requirement_status": requirement_status_for(claim),
        "can_act": can_act,
        "can_correct_day_type": can_act and claim.current_step == routing.STEP_HR,
        "day_type_choices": DAY_TYPE_CHOICES,
        "step_label": routing.STEP_LABELS.get(claim.current_step, ""),
        "actions": claim.actions.select_related("acted_by"),
    })


@login_required
def claim_action(request, pk):
    if request.method != "POST":
        return redirect("cto:claim_detail", pk=pk)
    employee = _employee_or_403(request)
    claim = get_object_or_404(CTOClaim, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "")
    today = timezone.localdate()
    try:
        with transaction.atomic():
            if action in ("add_line", "remove_line", "submit", "cancel"):
                if not _can_edit_claim(employee, claim):
                    raise PermissionDenied("You cannot change this claim.")
                if action == "add_line":
                    if claim.uses_ot_lines():
                        form = OTClaimLineForm(request.POST, ot_lines=claimable_ot_lines(claim.employee))
                    else:
                        form = ManualClaimLineForm(request.POST)
                    if not form.is_valid():
                        raise ValidationError([f"{k}: {', '.join(v)}" for k, v in form.errors.items()])
                    add_line(claim, form.cleaned_data["hours_worked"], today, **{
                        k: form.cleaned_data[k] for k in ("ot_line", "work_date", "day_type") if k in form.cleaned_data
                    })
                elif action == "remove_line":
                    claim.lines.filter(pk=request.POST.get("line")).delete()
                elif action == "submit":
                    submit_claim(claim, request.user, today)
                else:
                    claim.status = RoutedRecord.CANCELLED
                    claim.save(update_fields=["status", "updated_at"])
                    claim.actions.create(action="cancel", acted_by=request.user)
            elif action == "approve":
                approve_claim(claim, employee, request.user, notes)
            elif action == "return":
                routing.return_record(claim, employee, request.user, notes)
            elif action == "correct_day_type":
                line = get_object_or_404(CTOClaimLine, pk=request.POST.get("line"), claim=claim)
                correct_day_type(line, request.POST.get("day_type"), notes, employee, request.user)
            else:
                raise PermissionDenied("Unknown action.")
    except ValidationError as exc:
        _show_errors(request, exc)
    else:
        messages.success(request, "Done.")
    return redirect("cto:claim_detail", pk=pk)


# --- Opening balances (CLAUDE.md §7.1 Option A) -------------------------

@login_required
def opening_balances(request):
    actor = _employee_or_403(request)
    if not can_enter_opening(actor):
        raise PermissionDenied("Only an HR Administrator (or the AO when there is no other) enters opening balances.")
    today = timezone.localdate()
    target = None
    pk = request.POST.get("employee") or request.GET.get("employee")
    if pk:
        target = get_object_or_404(Employee, pk=pk)
    form = OpeningLineForm(request.POST or None)
    if request.method == "POST" and target is not None:
        try:
            if request.POST.get("action") == "void":
                line = get_object_or_404(CTOClaimLine, pk=request.POST.get("line"), claim__employee=target,
                                         claim__is_opening=True)
                void_opening_line(actor, line, request.POST.get("void_reason", ""), request.user, today)
                messages.success(request, "Line voided.")
            elif form.is_valid():
                add_opening_line(actor, target, form.cleaned_data["days"], form.cleaned_data["date_earned"],
                                 form.cleaned_data["reason"], request.user, today)
                messages.success(request, "Opening line added.")
            else:
                raise ValidationError([f"{k}: {', '.join(v)}" for k, v in form.errors.items()])
        except ValidationError as exc:
            _show_errors(request, exc)
        else:
            return redirect(f"{request.path}?employee={target.pk}")
    lines = (CTOClaimLine.objects.filter(claim__employee=target, claim__is_opening=True).order_by("work_date", "id")
             if target else [])
    return render(request, "cto/opening_balances.html", {
        "employees": Employee.objects.filter(is_active=True),
        "target": target,
        "lines": lines,
        "form": form,
        "entry_open": entry_is_open(today),
        "lock_date": settings.CTO_OPENING_BALANCE_DATE,
        "may_enter_for_target": target is not None and actor in opening_entry_actors(target),
    })


# --- Year-end carry-over and separation (Stage 2) -------------------------

@login_required
def carry_over(request):
    actor = _employee_or_403(request)
    today = timezone.localdate()
    year = today.year
    is_coh = is_chief_of_hospital(actor)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "submit":
                employee = get_object_or_404(Employee, pk=request.POST.get("employee"))
                carryover.submit_reason(actor, employee, year, request.POST.get("reason", ""), today)
                messages.success(request, f"Reason submitted for {employee}.")
            elif request.POST.get("action") in ("approve", "reject"):
                record = get_object_or_404(CTOCarryOverReason, pk=request.POST.get("reason_id"))
                carryover.decide_reason(actor, record, request.POST["action"] == "approve",
                                        request.POST.get("note", ""), today)
                messages.success(request, "Decision recorded.")
            else:
                raise PermissionDenied("Unknown action.")
        except ValidationError as exc:
            _show_errors(request, exc)
        return redirect("cto:carry_over")

    reasons = {r.employee_id: r for r in CTOCarryOverReason.objects.filter(year=year).select_related("submitted_by")}
    rows = []
    for employee in carryover.at_risk_employees(year):
        lines = carryover.at_risk_lines(employee, year)
        rows.append({
            "employee": employee,
            "days": sum((l.remaining for l in lines), 0),
            "reason": reasons.get(employee.pk),
            "can_submit": actor in carryover.reason_submitters(employee) and employee.pk not in reasons,
        })
    sees_all = is_hr(actor, True) or is_administrative_officer(actor, True) or is_chief_of_hospital(actor, True)
    if not sees_all:
        rows = [r for r in rows if r["can_submit"] or (r["reason"] and r["reason"].submitted_by_id == actor.pk)]
    return render(request, "cto/carry_over.html", {
        "year": year,
        "rows": rows,
        "pending": CTOCarryOverReason.objects.filter(status=CTOCarryOverReason.PENDING).select_related(
            "employee", "submitted_by") if is_coh else [],
        "reason_deadline": carryover.reason_deadline(year),
        "submission_open": today <= carryover.reason_deadline(year),
    })


@login_required
def separation(request):
    actor = _employee_or_403(request)
    if not can_set_separation(actor):
        raise PermissionDenied("Only HR can set a separation status.")
    form = SeparationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            set_separation(actor, form.cleaned_data["employee"], form.cleaned_data["separation_status"],
                           form.cleaned_data["last_day_of_service"], form.cleaned_data["reason"], request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Separation status saved.")
            return redirect("cto:separation")
    return render(request, "cto/separation.html", {
        "form": form,
        "separating": Employee.objects.exclude(separation_status="").order_by("last_day_of_service"),
    })


# --- CTO usage -----------------------------------------------------------

@login_required
def my_cto(request):
    employee = _employee_or_403(request)
    return render(request, "cto/my_cto.html", {
        "balance": compute_available_cto_balance(employee),
        "applications": employee.cto_usage_applications.all(),
        "claims": employee.cto_claims.filter(is_opening=False),
        "credit_lines": CTOClaimLine.objects.filter(claim__employee=employee, state=CTOClaimLine.CREDITED),
        "claimable_ot_lines": claimable_ot_lines(employee),
        "today": timezone.localdate(),
    })


@login_required
def cto_apply(request):
    employee = _employee_or_403(request)
    if not is_cto_eligible(employee):
        raise PermissionDenied("CTO is not applicable to the Chief of Hospital.")
    today = timezone.localdate()

    if request.method == "POST":
        form = CTOUsageApplicationForm(request.POST, employee=employee)
        if form.is_valid():
            start = form.cleaned_data["start_date"]
            end = form.cleaned_data["end_date"]
            days = form.cleaned_data["number_of_days"]
            application = None
            with transaction.atomic():
                # Lock the employee row so two applications filed at once can't
                # both pass the balance/cap checks and hold the same credit.
                Employee.objects.select_for_update().get(pk=employee.pk)
                employee.refresh_from_db()  # separation status as of now
                separating = is_separating(employee)
                plan = plan_allocation(employee, days, today, end)
                if separating and end > employee.last_day_of_service:
                    form.add_error("end_date", f"CTO can only be used up to your last day of service "
                                               f"({employee.last_day_of_service:%B %d, %Y}).")
                elif not separating and exceeds_monthly_cap(employee, start, days):
                    form.add_error(None, "This would exceed the 5-CTO-day-per-month usage cap.")
                elif not separating and forms_illegal_consecutive_run(employee, start, end):
                    form.add_error(None, "CTO cannot be taken for 3 or more consecutive days.")
                elif plan is None:
                    form.add_error(None, "Not enough CTO credit can be used on these dates. Credit counts from its "
                                         "effective date and must be used by its use deadline (normally Dec 15).")
                else:
                    application = form.save(commit=False)
                    application.employee = employee
                    application.full_clean()
                    application.save()
                    CTOCreditAllocation.objects.bulk_create(
                        CTOCreditAllocation(line=line, usage_application=application, days=d) for line, d in plan
                    )
                    CTOUsageApplicationAction.objects.create(
                        application=application, action="submit",
                        resulting_status=application.status, acted_by=request.user,
                    )
            if application is not None:
                notify_status_change(application)
                messages.success(request, "CTO application submitted.")
                return redirect("cto:my_cto")
    else:
        form = CTOUsageApplicationForm(employee=employee)

    return render(request, "cto/cto_apply.html", {"form": form, "balance": compute_available_cto_balance(employee)})


@login_required
def cto_queue(request):
    employee = get_acting_employee(request.user)
    pending_ot = [ot for ot in OvertimeRequest.objects.filter(status=RoutedRecord.PENDING) if routing.can_act(employee, ot)]
    pending_claims = [
        c for c in CTOClaim.objects.filter(status=RoutedRecord.PENDING).select_related("employee")
        if routing.can_act(employee, c)
    ]
    return render(request, "cto/cto_queue.html", {
        # oldest first: first filed, first acted on (owner, 2026-10-06)
        "applications": visible_cto_applications_for(employee).select_related("employee").order_by("submitted_at", "pk"),
        "pending_ot": pending_ot,
        "pending_claims": pending_claims,
        "my_filed_ot": OvertimeRequest.objects.filter(filed_by=employee) if employee else [],
        "can_file_ot": bool(filing_capacities(employee)),
        "can_file_exception": employee is not None and employee.is_hr_administrator(),
        "can_enter_opening": can_enter_opening(employee),
        "can_set_separation": can_set_separation(employee),
    })


@login_required
def cto_action(request, pk):
    """Single POST endpoint for every CTO usage routing step. Approval needs
    no balance check: the days were held against credit lines at filing.
    Returning, rejecting or cancelling releases the hold."""
    acting_employee = _employee_or_403(request)
    application = get_object_or_404(CTOUsageApplication, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()

    status = application.status
    filers = [application.employee]  # never acts on their own (owner decision 2026-10-26)
    # (may act, step action, status it leads to) - next level up and two-level
    # endorsement from leave.permissions (owner decisions 2026-10-26/27).
    if status == CTOUsageApplication.SUBMITTED:
        unit = supervisor_step_result(acting_employee, filers) == UNIT_STEP
        expected = (can_endorse(acting_employee, filers), "endorse",
                    CTOUsageApplication.ENDORSED_BY_UNIT_HEAD if unit else CTOUsageApplication.ENDORSED_BY_SUPERVISOR)
    else:
        expected = {
            CTOUsageApplication.ENDORSED_BY_UNIT_HEAD: (
                can_endorse_second(acting_employee, filers), "endorse", CTOUsageApplication.ENDORSED_BY_SUPERVISOR),
            CTOUsageApplication.ENDORSED_BY_SUPERVISOR: (
                can_act_hr_step(acting_employee, filers), "process", CTOUsageApplication.PROCESSED_BY_HR),
            CTOUsageApplication.PROCESSED_BY_HR: (
                can_act_ao_step(acting_employee, filers), "recommend", CTOUsageApplication.RECOMMENDED_BY_AO),
            CTOUsageApplication.RECOMMENDED_BY_AO: (
                can_act_coh_step(acting_employee, filers), "approve", CTOUsageApplication.APPROVED),
        }.get(status)

    if expected is None or not expected[0]:
        raise PermissionDenied("You are not authorized to take this action on this application.")
    if action == expected[1]:
        new_status = expected[2]
        if new_status == CTOUsageApplication.ENDORSED_BY_UNIT_HEAD:
            action = "endorse_unit"
    elif action == "reject":
        new_status = CTOUsageApplication.REJECTED
    elif action == "return":
        new_status = CTOUsageApplication.RETURNED
    else:
        raise PermissionDenied("You are not authorized to take this action on this application.")

    with transaction.atomic():
        if new_status in (CTOUsageApplication.REJECTED, CTOUsageApplication.RETURNED):
            application.allocations.all().delete()
        application.status = new_status
        application.save(update_fields=["status", "updated_at"])
        CTOUsageApplicationAction.objects.create(
            application=application, action=action, resulting_status=new_status, notes=notes, acted_by=request.user,
        )
    notify_status_change(application)
    messages.success(request, "Action recorded.")
    return redirect("cto:cto_queue")


@login_required
def print_cto_form(request, pk):
    """Form No. BDH-ADM-HR-01F04-B (see cto/cto_form.py)."""
    from .cto_form import render_pdf

    acting_employee = get_acting_employee(request.user)
    application = get_object_or_404(CTOUsageApplication, pk=pk)
    if not can_view_cto_application(acting_employee, application):
        raise PermissionDenied("You are not authorized to view this CTO application.")
    return pdf_response(
        request, lambda: render_pdf(application, printed_by=request.user),
        f"CTO-Application_{application.employee.surname}_{application.pk}.pdf", "cto:my_cto",
    )
