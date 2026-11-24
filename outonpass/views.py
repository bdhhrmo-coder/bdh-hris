"""Out-on-Pass screens, stage 2: filing, the pass page, the approval queue
and every routing action (CLAUDE.md 13B). Tap Out / Tap In come in stage 3."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment
from documents.models import UploadedDocument, UploadedDocumentEvent
from documents.permissions import can_view_transaction
from employees.models import Employee
from employees.permissions import get_acting_employee
from leave.permissions import RETURN_NEEDS_REMARK

from . import routing, rules
from .forms import ActionForm, PassForm
from .models import OutOnPass as P
from .models import OutOnPassAction
from .notifications import notify_next


def _acting(request):
    acting = get_acting_employee(request.user)
    if acting is None:
        raise PermissionDenied("No employee record is linked to your account.")
    return acting


def _proofs(p):
    ct = ContentType.objects.get_for_model(P)
    return UploadedDocument.objects.filter(content_type=ct, object_id=p.pk, is_active=True)


def _save_proof(p, form, user):
    f = form.cleaned_data.get("proof")
    if not f:
        return
    doc = UploadedDocument.objects.create(
        content_type=ContentType.objects.get_for_model(P), object_id=p.pk, file=f,
        original_filename=f.name, file_size=f.size, file_type=form.proof_file_type,
        is_confidential=True, uploaded_by=user)
    UploadedDocumentEvent.objects.create(document=doc, event_type=UploadedDocumentEvent.UPLOADED, acted_by=user)


def _log(p, user, action, notes=""):
    OutOnPassAction.objects.create(out_on_pass=p, action=action, resulting_status=p.status,
                                   notes=notes, acted_by=user)


def _check(employee, data, has_proof, exclude_pk=None):
    return rules.check_pass(employee, data["kind"], data["pass_date"], data["time_out"], data["time_in"],
                            has_proof=has_proof, exclude_pk=exclude_pk)


def _form_page(request, acting, form, violations, p=None):
    on = form["pass_date"].value() if form.is_bound else None
    try:
        on_date = date.fromisoformat(str(on)) if on else timezone.localdate()
    except ValueError:
        on_date = timezone.localdate()
    return render(request, "outonpass/form.html", {
        "form": form, "violations": violations, "p": p,
        "bal": rules.balance_panel(rules.summary(acting, on_date)),
        "is_coh": acting.has_role(RoleAssignment.CHIEF_OF_HOSPITAL),
    })


@login_required
def file_pass(request):
    acting = _acting(request)
    violations = []
    if request.method == "POST":
        form = PassForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                Employee.objects.select_for_update().get(pk=acting.pk)  # one filing at a time per person
                violations = _check(acting, form.cleaned_data, has_proof=bool(form.cleaned_data.get("proof")))
                if not violations:
                    p = form.save(commit=False)
                    p.employee = acting
                    p.filed_by = request.user
                    p.recorded_only = acting.has_role(RoleAssignment.CHIEF_OF_HOSPITAL)
                    p.status = P.SUBMITTED
                    p.save()
                    _save_proof(p, form, request.user)
                    _log(p, request.user, "submit")
            if not violations:
                notify_next(p)
                messages.success(request, f"Pass {p.pass_number} filed. "
                                          + ("HR will record it." if p.recorded_only else
                                             "Wait for it to be approved before you leave."))
                return redirect("outonpass:detail", p.pk)
    else:
        form = PassForm(initial={"pass_date": timezone.localdate(), "kind": P.ORDINARY})
    return _form_page(request, acting, form, violations)


@login_required
def edit_pass(request, pk):
    acting = _acting(request)
    p = get_object_or_404(P, pk=pk)
    if not routing.can_edit(acting, p):
        raise PermissionDenied("Only a returned pass can be corrected, and only by the person who filed it.")
    has_proof = _proofs(p).exists()
    violations = []
    if request.method == "POST":
        form = PassForm(request.POST, request.FILES, instance=p, has_proof=has_proof)
        if form.is_valid():
            with transaction.atomic():
                Employee.objects.select_for_update().get(pk=acting.pk)
                violations = _check(acting, form.cleaned_data,
                                    has_proof=has_proof or bool(form.cleaned_data.get("proof")), exclude_pk=p.pk)
                if not violations:
                    p = form.save(commit=False)
                    p.status = P.SUBMITTED
                    p.recorded_only = acting.has_role(RoleAssignment.CHIEF_OF_HOSPITAL)
                    p.confirmed_coverage = p.confirmed_no_surge = p.confirmed_shift_hours = False
                    p.covering_staff = ""
                    p.save()
                    _save_proof(p, form, request.user)
                    _log(p, request.user, "resubmit")
            if not violations:
                notify_next(p)
                messages.success(request, f"Pass {p.pass_number} resubmitted. It starts again from the first step.")
                return redirect("outonpass:detail", p.pk)
    else:
        form = PassForm(instance=p, has_proof=has_proof)
    return _form_page(request, acting, form, violations, p)


@login_required
def my_passes(request):
    acting = _acting(request)
    passes = acting.out_on_passes.all()[:100]
    return render(request, "outonpass/mine.html", {
        "passes": passes, "today_passes": [p for p in passes if p.pass_date == timezone.localdate()
                                          and p.status in P.CLEARED_STATUSES],
        "bal": rules.balance_panel(rules.summary(acting, timezone.localdate())),
    })


@login_required
def queue(request):
    acting = _acting(request)
    from . import gate as g
    return render(request, "outonpass/queue.html", {"passes": routing.waiting_for(acting),
                                                    "missed": g.missed_waiting_for(acting)})


@login_required
def detail(request, pk):
    acting = _acting(request)
    p = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    if not can_view_transaction(acting, p):
        raise PermissionDenied("You are not authorized to view this pass.")
    actions = routing.allowed_actions(acting, p)
    return render(request, "outonpass/detail.html", {
        "p": p, "history": p.actions.select_related("acted_by__employee"),
        "actions": actions,
        "forward": actions[0] if actions else None,
        "forward_label": routing.ACTION_LABELS.get(actions[0]) if actions else "",
        "supervisor_step": bool(actions) and routing.is_supervisor_step(p),
        "needs_shift": rules.needs_shift_confirmation(p.employee, p.pass_date),
        "can_cancel": routing.can_cancel(acting, p),
        "can_edit": routing.can_edit(acting, p),
        "bal": rules.balance_panel(rules.summary(p.employee, p.pass_date)),
        "proofs": _proofs(p) if routing.may_view_proof(acting, p) else None,
        "has_proof": _proofs(p).exists(),
        "tap_out_problem": _tap_problem(acting, p, "out"), "tap_in_problem": _tap_problem(acting, p, "in"),
        "is_owner": acting.pk == p.employee_id,
        "missed": [(m, gate.can_act_missed(acting, m), gate.NEXT.get(m.status)) for m in p.missed_taps.all()],
        "can_miss_out": gate.missed_tap_problem(acting, p, "OUT") is None,
        "can_miss_in": gate.missed_tap_problem(acting, p, "IN") is None,
        "hr_can_close": gate.hr_close_problem(acting, p) is None,
        "hr_can_mark_unused": gate.hr_unused_problem(acting, p) is None,
    })


def _tap_problem(acting, p, which):
    from . import gate as g
    return g.tap_out_problem(acting, p) if which == "out" else g.tap_in_problem(acting, p)


@login_required
def act(request, pk):
    if request.method != "POST":
        return redirect("outonpass:detail", pk)
    acting = _acting(request)
    form = ActionForm(request.POST)
    if not form.is_valid():
        raise PermissionDenied("Invalid action.")
    data = form.cleaned_data
    action, notes = data["action"], data["notes"].strip()

    with transaction.atomic():
        p = get_object_or_404(P.objects.select_for_update(), pk=pk)

        if action == routing.CANCEL:
            if not routing.can_cancel(acting, p):
                raise PermissionDenied("This pass can no longer be cancelled.")
            p.status = P.CANCELLED
            p.save(update_fields=["status"])
            _log(p, request.user, action, notes)
            messages.success(request, "Pass cancelled.")
            return redirect("outonpass:detail", pk)

        if action not in routing.allowed_actions(acting, p):
            raise PermissionDenied("You are not authorized to take this action on this pass.")

        if action in (routing.RETURN, routing.REJECT):
            if not notes:
                messages.error(request, RETURN_NEEDS_REMARK)
                return redirect("outonpass:detail", pk)
        else:
            problems = []
            if routing.is_supervisor_step(p):
                if not data["confirmed_coverage"]:
                    problems.append("Confirm that the unit can be covered.")
                if not data["confirmed_no_surge"]:
                    problems.append("Confirm that there is no surge or Code activation.")
                if p.kind == P.THIRD and not data["covering_staff"].strip():
                    problems.append("Name the staff covering the unit (third pass).")
                if rules.needs_shift_confirmation(p.employee, p.pass_date) and not data["confirmed_shift_hours"]:
                    problems.append("There is no approved duty schedule for this month: confirm the pass is not "
                                    "in the first or last hour of the employee's shift.")
            # The rules are checked again: time passes and other passes are filed.
            problems += [v.message for v in rules.check_pass(
                p.employee, p.kind, p.pass_date, p.time_out, p.time_in,
                has_proof=_proofs(p).exists(), exclude_pk=p.pk)]
            if problems:
                for m in problems:
                    messages.error(request, m)
                messages.error(request, "Nothing was changed. If the pass can no longer go ahead, return or "
                                        "reject it with a remark.")
                return redirect("outonpass:detail", pk)
            if routing.is_supervisor_step(p):
                p.confirmed_coverage = p.confirmed_no_surge = True
                p.confirmed_shift_hours = data["confirmed_shift_hours"]
                p.covering_staff = data["covering_staff"].strip()

        p.status = routing.result_of(action, p)
        p.save()
        _log(p, request.user, action, notes)

    notify_next(p)
    messages.success(request, f"{routing.ACTION_LABELS[action]}: done. The pass is now "
                              f"{p.get_status_display().lower()}.")
    return redirect("outonpass:queue")


@login_required
def guard_card(request, pk):
    """The approved pass as the employee shows it to the guard on duty (a
    photo or the screen itself). Read-only; the official gate times are the
    HRIS Tap Out / Tap In (stage 3)."""
    acting = _acting(request)
    p = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    if not can_view_transaction(acting, p):
        raise PermissionDenied("You are not authorized to view this pass.")
    cleared = p.status in P.CLEARED_STATUSES + (P.COMPLETED,)
    decided = p.actions.filter(action__in=["approve", "record"]).select_related("acted_by__employee").last()
    approval = None
    if decided:
        who = getattr(decided.acted_by, "employee", None)
        approval = {"name": who.full_name if who else decided.acted_by.username,
                    "position": (who.position if who else "") or "", "when": decided.acted_at}
    e = p.employee
    units = ", ".join(u.name for u in e.units.all())
    sections = ", ".join(s.name for s in e.sections.all())
    if e.employment_status == Employee.EMPLOYMENT_STATUS_COSP:
        employment = "PGSS (Security)" if e.cosp_under == Employee.COSP_UNDER_PGSS else "COSP"
    else:
        employment = "Permanent"
    return render(request, "outonpass/guard_card.html", {
        "p": p, "cleared": cleared, "approval": approval, "now": timezone.localtime(),
        "area": " / ".join(x for x in (units, sections) if x), "employment": employment,
    })


# -- Stage 3: Tap Out / Tap In, missed taps, the Gate page ------------------

from . import gate  # noqa: E402
from .models import MissedTap  # noqa: E402


@login_required
def tap(request, pk):
    if request.method != "POST":
        return redirect("outonpass:detail", pk)
    acting = _acting(request)
    which = request.POST.get("which")
    with transaction.atomic():
        p = get_object_or_404(P.objects.select_for_update(), pk=pk)
        problem = gate.tap_out_problem(acting, p) if which == "out" else gate.tap_in_problem(acting, p)
        if problem:
            messages.error(request, problem)
            return redirect("outonpass:detail", pk)
        if which == "out":
            gate.tap_out(p, request.user)
            messages.success(request, f"Tapped out at {timezone.localtime(p.tapped_out_at):%I:%M %p}. "
                                      f"Show pass {p.pass_number} to the guard. Tap In when you are back.")
        else:
            gate.tap_in(p, request.user)
            msg = f"Welcome back. Tapped in at {timezone.localtime(p.tapped_in_at):%I:%M %p}; " \
                  f"{rules.hm(p.minutes_charged)} charged."
            if p.excess_minutes or p.over_balance_minutes:
                msg += " Part of this time is not covered by the pass; HR will see it."
            messages.success(request, msg)
    return redirect("outonpass:detail", pk)


def _parse_local(value):
    from datetime import datetime
    try:
        return timezone.make_aware(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return None


@login_required
def missed_new(request, pk, which):
    acting = _acting(request)
    p = get_object_or_404(P, pk=pk)
    which = MissedTap.OUT if which == "out" else MissedTap.IN
    problem = gate.missed_tap_problem(acting, p, which)
    if problem:
        messages.error(request, problem)
        return redirect("outonpass:detail", pk)
    error = None
    if request.method == "POST":
        claimed = _parse_local(request.POST.get("claimed_at", ""))
        reason = request.POST.get("reason", "").strip()
        if claimed is None:
            error = "Enter the date and time."
        elif not reason:
            error = "Say why the tap was missed."
        else:
            error = gate.claimed_time_problem(p, which, claimed)
        if not error:
            m = MissedTap.objects.create(out_on_pass=p, which=which, claimed_at=claimed, reason=reason[:255],
                                         filed_by=request.user)
            m.actions.create(action="submit", resulting_status=m.status, acted_by=request.user)
            from notifications.services import notify
            notify(gate.missed_step_actors(m),
                   f"{p.employee} reported a missed {m.get_which_display()} on pass {p.pass_number}: "
                   "please confirm it.", reverse("outonpass:detail", args=[p.pk]))
            messages.success(request, "Reported. Your supervisor confirms it, the guard on duty verifies the time, "
                                      "and HRMO reviews it before the hours are charged.")
            return redirect("outonpass:detail", pk)
    default = timezone.localtime().replace(second=0, microsecond=0)
    return render(request, "outonpass/missed_form.html", {
        "p": p, "which": which, "label": dict(MissedTap.WHICH_CHOICES)[which], "error": error,
        "value": request.POST.get("claimed_at") or f"{default:%Y-%m-%dT%H:%M}",
        "reason": request.POST.get("reason", ""),
    })


@login_required
def missed_act(request, pk):
    if request.method != "POST":
        raise PermissionDenied("Invalid action.")
    acting = _acting(request)
    with transaction.atomic():
        m = get_object_or_404(MissedTap.objects.select_for_update().select_related("out_on_pass"), pk=pk)
        p = m.out_on_pass
        if not gate.can_act_missed(acting, m):
            raise PermissionDenied("You are not authorized to act on this missed tap.")
        action = request.POST.get("action")
        notes = request.POST.get("notes", "").strip()[:255]
        if action == "reject" and not notes:
            messages.error(request, RETURN_NEEDS_REMARK)
            return redirect(request.POST.get("next") or reverse("outonpass:detail", args=[p.pk]))
        verified_at = None
        if m.status == MissedTap.CONFIRMED and action == "verify" and request.POST.get("verified_at"):
            verified_at = _parse_local(request.POST["verified_at"])
            problem = "Enter a valid time." if verified_at is None else gate.claimed_time_problem(p, m.which, verified_at)
            if problem:
                messages.error(request, problem)
                return redirect(request.POST.get("next") or reverse("outonpass:detail", args=[p.pk]))
            if verified_at != m.claimed_at and not notes:
                messages.error(request, "You changed the time: add a remark saying why.")
                return redirect(request.POST.get("next") or reverse("outonpass:detail", args=[p.pk]))
        try:
            gate.act_on_missed(m, request.user, action, notes, verified_at)
        except (ValueError, KeyError):
            raise PermissionDenied("That action is not available at this step.")
    from notifications.services import notify
    url = reverse("outonpass:detail", args=[p.pk])
    if m.status in MissedTap.PENDING_STATUSES:
        step = {MissedTap.CONFIRMED: "verify the time (Gate page)", MissedTap.VERIFIED: "review it"}[m.status]
        notify(gate.missed_step_actors(m), f"Missed {m.get_which_display()} on pass {p.pass_number} "
                                           f"({p.employee}): please {step}.", url)
    elif m.status == MissedTap.REVIEWED:
        notify(p.employee, f"Your missed {m.get_which_display()} on pass {p.pass_number} was recorded.", url)
    else:
        notify(p.employee, f"Your missed {m.get_which_display()} on pass {p.pass_number} was not accepted. "
                           f'Remark: "{notes}". You may report a corrected one.', url)
    messages.success(request, f"Done. The missed tap is now {m.get_status_display().lower()}.")
    return redirect(request.POST.get("next") or url)


@login_required
def gate_page(request):
    acting = _acting(request)
    if not gate.can_view_gate(acting):
        raise PermissionDenied("The Gate page is for the guards on duty and HR.")
    to_verify = [m for m in MissedTap.objects.filter(status=MissedTap.CONFIRMED)
                 .select_related("out_on_pass__employee") if gate.can_act_missed(acting, m)]
    return render(request, "outonpass/gate.html", {
        "passes": gate.gate_passes(), "to_verify": to_verify, "now": timezone.localtime(),
        "not_back": gate.not_back() if not gate.is_guard(acting) else [],
        "unused": gate.unused() if not gate.is_guard(acting) else [],
    })


@login_required
def hr_close(request, pk):
    """HR closes a pass with no Tap In (enters the time in), or marks a never-used pass. Remark required."""
    if request.method != "POST":
        return redirect("outonpass:detail", pk)
    acting = _acting(request)
    what = request.POST.get("what")
    remark = request.POST.get("remark", "").strip()
    with transaction.atomic():
        p = get_object_or_404(P.objects.select_for_update(), pk=pk)
        problem = gate.hr_close_problem(acting, p) if what == "close" else gate.hr_unused_problem(acting, p)
        if problem:
            raise PermissionDenied(problem)
        if not remark:
            messages.error(request, "Type a remark saying how the time was established. Nothing was changed.")
            return redirect("outonpass:detail", pk)
        if what == "close":
            time_in = _parse_local(request.POST.get("time_in", ""))
            if time_in is None or time_in <= p.tapped_out_at or time_in > gate.current_time():
                messages.error(request, "Enter a time in after the Tap Out and not in the future. Nothing was changed.")
                return redirect("outonpass:detail", pk)
            gate.hr_close(p, request.user, time_in, remark)
            messages.success(request, f"Pass closed: {rules.hm(p.minutes_charged)} charged.")
        else:
            gate.hr_mark_unused(p, request.user, remark)
            messages.success(request, "Pass marked not used. It no longer holds the balance.")
    from notifications.services import notify
    notify(p.employee, f"HR closed your pass {p.pass_number}. Remark: \"{remark}\"",
           reverse("outonpass:detail", args=[p.pk]))
    return redirect("outonpass:detail", pk)


# -- Stage 4: slip printout, monthly report, HR setup ------------------------

@login_required
def print_slip(request, pk):
    """BDH-ADM-AO-01F16 Rev 1 filled in from the pass (digital stamps)."""
    from printouts.http import pdf_response
    from .slip import render_pdf

    acting = _acting(request)
    p = get_object_or_404(P.objects.select_related("employee"), pk=pk)
    if not can_view_transaction(acting, p):
        raise PermissionDenied("You are not authorized to view this pass.")
    return pdf_response(request, lambda: render_pdf(p), f"OutOnPass_{p.pass_number}.pdf", "outonpass:mine")


def _can_view_report(acting):
    return acting is not None and any(acting.has_role(r, viewing=True) for r in (
        RoleAssignment.HR_PROCESSOR, RoleAssignment.HR_ADMINISTRATOR,
        RoleAssignment.ADMINISTRATIVE_OFFICER, RoleAssignment.CHIEF_OF_HOSPITAL))


@login_required
def monthly_report(request):
    from dashboard.exports import _pdf_response, _xlsx_response
    from . import report

    acting = _acting(request)
    if not _can_view_report(acting):
        raise PermissionDenied("The Out-on-Pass report is for HR, the AO and the Chief of Hospital.")
    today = timezone.localdate()
    try:
        year, month = (int(x) for x in request.GET.get("month", f"{today:%Y-%m}").split("-"))
        date(year, month, 1)
    except (ValueError, TypeError):
        year, month = today.year, today.month
    r = report.monthly(year, month)
    fmt = request.GET.get("format")
    if fmt in ("xlsx", "pdf"):
        tables = report.export_tables(r)
        name = f"out_on_pass_report_{year}-{month:02d}"
        if fmt == "xlsx":
            short = ["By unit", "Special passes", "Time not covered", "Out not back", "Never tapped out",
                     "Low balances", "Repeated third passes"]
            return _xlsx_response(name, [(s, hdr, rows) for s, (h, hdr, rows) in zip(short, tables)])
        return _pdf_response(name, f"BDH HRIS &mdash; Out-on-Pass Report, {r['label']}", tables)
    return render(request, "outonpass/report.html", {"r": r, "month_value": f"{year}-{month:02d}"})


def _is_hr_admin(acting):
    return acting is not None and acting.has_role(RoleAssignment.HR_ADMINISTRATOR)


@login_required
def setup(request):
    """HR Administrator: COSP contract periods (the COSP allowance) and the one-off
    hours already used Jan 1-10, 2027 (Google Sheets tracker). Nobody enters their
    own; a wrong line is voided with a reason, never edited or deleted."""
    from .forms import ContractForm, OpeningForm
    from .models import CospContractPeriod, PassOpeningUsage

    acting = _acting(request)
    if not _is_hr_admin(acting):
        raise PermissionDenied("Out-on-Pass setup is for the HR Administrator.")
    contract_form = ContractForm(acting=acting)
    opening_form = OpeningForm(acting=acting)
    if request.method == "POST":
        what = request.POST.get("what")
        if what == "contract":
            contract_form = ContractForm(request.POST, acting=acting)
            if contract_form.is_valid():
                c = contract_form.save(commit=False)
                c.entered_by = request.user
                c.save()
                messages.success(request, f"Contract period saved for {c.employee}.")
                return redirect("outonpass:setup")
        elif what == "opening":
            opening_form = OpeningForm(request.POST, acting=acting)
            if opening_form.is_valid():
                o = opening_form.save(commit=False)
                o.entered_by = request.user
                o.save()
                messages.success(request, f"Hours already used saved for {o.employee}.")
                return redirect("outonpass:setup")
        elif what in ("void_contract", "void_opening"):
            model = CospContractPeriod if what == "void_contract" else PassOpeningUsage
            line = get_object_or_404(model, pk=request.POST.get("pk"), voided_at__isnull=True)
            reason = request.POST.get("reason", "").strip()
            if line.employee_id == acting.pk:
                raise PermissionDenied("Another HR Administrator must change your own records.")
            if not reason:
                messages.error(request, "Type the reason for voiding. Nothing was changed.")
            else:
                line.voided_at, line.voided_by, line.void_reason = timezone.now(), request.user, reason[:255]
                line.save()
                messages.success(request, "Voided. Enter the correct line if needed.")
            return redirect("outonpass:setup")
    return render(request, "outonpass/setup.html", {
        "contract_form": contract_form, "opening_form": opening_form,
        "contracts": CospContractPeriod.objects.select_related("employee", "entered_by__employee", "voided_by__employee")
                                                .order_by("employee__surname", "start_date"),
        "openings": PassOpeningUsage.objects.select_related("employee", "entered_by__employee", "voided_by__employee")
                                            .order_by("employee__surname"),
    })
