"""Out-on-Pass screens, stage 2: filing, the pass page, the approval queue
and every routing action (CLAUDE.md 13B). Tap Out / Tap In come in stage 3."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
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
        "summary": rules.summary(acting, on_date),
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
        "passes": passes, "summary": rules.summary(acting, timezone.localdate()),
    })


@login_required
def queue(request):
    acting = _acting(request)
    return render(request, "outonpass/queue.html", {"passes": routing.waiting_for(acting)})


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
        "summary": rules.summary(p.employee, p.pass_date),
        "proofs": _proofs(p) if routing.may_view_proof(acting, p) else None,
        "has_proof": _proofs(p).exists(),
    })


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
