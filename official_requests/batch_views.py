"""Views for group (batch) filing - Batch 2, Item 7. Rules live in batch.py."""


from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from leave.permissions import is_administrative_officer, is_chief_of_hospital, is_hr, is_supervisor_of
from orgstructure.models import Section

from . import batch as rules
from .forms import BatchForm
from .models import OfficialRequest, OfficialRequestBatch
from .permissions import get_acting_employee



def _with_edit_flag(batches):
    """Mark each of the filer's batches that can still be edited before anyone acts."""
    batches = list(batches)
    for b in batches:
        b.can_edit_pending = rules.pending_untouched(b)
    return batches

def _employee_options(acting_employee):
    emps = rules.pickable_employees(acting_employee).exclude(pk=acting_employee.pk).prefetch_related("sections")
    return [{"id": e.pk, "name": e.full_name, "sections": [s.pk for s in e.sections.all()]} for e in emps]


def _section_options(acting_employee):
    """Sections offered for 'Select all in section': for a Supervisor, the
    ones they supervise; for HR, all of them."""
    roles = rules.filer_roles(acting_employee)
    if any(r in rules.ANY_EMPLOYEE_ROLES for r in roles):
        return list(Section.objects.filter(is_active=True).order_by("name").values("id", "name"))
    ids = set(acting_employee.active_role_assignments().filter(role="SUPERVISOR").values_list("section_id", flat=True))
    return list(Section.objects.filter(pk__in=ids - {None}).order_by("name").values("id", "name"))


def _rows_from_post(data):
    total = int(data.get("lines-TOTAL_FORMS") or 0)
    return [{"date": data.get(f"lines-{i}-date", ""), "time_from": data.get(f"lines-{i}-time_from", ""),
             "time_to": data.get(f"lines-{i}-time_to", ""), "employee": data.get(f"lines-{i}-employee", ""),
             "note": data.get(f"lines-{i}-note", "")} for i in range(min(total, rules.MAX_ENTRIES))]


def _rows_from_batch(batch):
    return [{"date": line.start_date.isoformat(), "time_from": line.time_from.strftime("%H:%M") if line.time_from else "",
             "time_to": line.time_to.strftime("%H:%M") if line.time_to else "", "employee": str(line.employee_id),
             "note": line.line_note} for line in batch.active_lines()]


def _form_context(request, form, acting_employee, rows, selected, conflicts=None, batch=None):
    return {
        "form": form, "batch": batch,
        "editor": ({
            "employees": _employee_options(acting_employee), "sections": _section_options(acting_employee),
            "selected": [str(x) for x in selected], "rows": rows or [{"date": "", "time_from": "", "time_to": "",
                                                                    "employee": "", "note": ""}],
            "applySame": form["apply_same"].value() if form.is_bound else (batch is None),
            "kind": form["kind"].value() or "", "maxLines": rules.MAX_DATE_LINES,
        }),
        "conflicts": conflicts or [],
        "work_kinds": sorted(rules.WORK_KINDS),
        "kind_rules": {k: rules.KINDS[k][0] for k in rules.KINDS},
        "my_batches": _with_edit_flag(OfficialRequestBatch.objects.filter(filed_by=request.user).order_by("-filed_at")[:15]),
    }


def _conflict_rows(entries, found):
    return [{"key": f'{e["employee"].pk}|{e["date"].isoformat()}', "employee": e["employee"].full_name,
             "date": e["date"], "reasons": found[(e["employee"].pk, e["date"])]}
            for e in entries if (e["employee"].pk, e["date"]) in found]


def _drop_skipped(entries, data):
    skip = set(data.getlist("skip"))
    return [e for e in entries if f'{e["employee"].pk}|{e["date"].isoformat()}' not in skip]


@login_required
def batch_file(request):
    acting_employee = get_acting_employee(request.user)
    if not rules.filer_roles(acting_employee):
        raise PermissionDenied("Group filing is for Supervisors, HR Processors, HR Administrators and the AO.")

    if request.method == "POST":
        form = BatchForm(request.POST, acting_employee=acting_employee)
        if form.is_valid():
            entries = _drop_skipped(form.entries, request.POST)
            found = rules.find_conflicts(entries)
            if not entries:
                form.add_error(None, "Every line was removed - nothing left to file.")
            elif not found:
                c = form.cleaned_data
                batch = rules.create_batch(request.user, form.filer_role(), c["kind"], c["purpose"].strip(),
                                           (c.get("destination") or "").strip(), entries,
                                           c.get("expenses_authorized") or "",
                                           (c.get("transportation_allowed") or "").strip(),
                                           (c.get("past_date_reason") or "").strip())
                messages.success(request, f"Group request #{batch.pk} filed for {len(entries)} employee-date line(s).")
                return redirect("official_requests:batch_detail", pk=batch.pk)
            ctx = _form_context(request, form, acting_employee, _rows_from_post(request.POST),
                                request.POST.getlist("employees"), _conflict_rows(entries, found))
            return render(request, "official_requests/batch_form.html", ctx)
        return render(request, "official_requests/batch_form.html", _form_context(
            request, form, acting_employee, _rows_from_post(request.POST), request.POST.getlist("employees")))

    form = BatchForm(acting_employee=acting_employee)
    return render(request, "official_requests/batch_form.html", _form_context(request, form, acting_employee, None, []))


@login_required
def batch_edit(request, pk):
    acting_employee = get_acting_employee(request.user)
    batch = get_object_or_404(OfficialRequestBatch, pk=pk)
    if batch.filed_by_id != request.user.pk:
        raise PermissionDenied("Only the person who filed this group request can edit it.")
    if not rules.filer_may_edit(batch, request.user):
        messages.error(request, "This group request can no longer be edited: an approver has already acted on it. "
                                "Ask them to return it if a change is needed.")
        return redirect("official_requests:batch_detail", pk=pk)
    kind = next(k for k, v in rules.KINDS.items() if v[1] == batch.request_type and v[2] == batch.work_kind)
    if rules.filing_role_for(acting_employee, kind) != batch.filer_role:  # the role decides the route
        raise PermissionDenied("Your roles have changed since this group request was filed. File a new one.")
    latest_return = batch.actions.filter(action="return").order_by("-acted_at").first()

    if request.method == "POST":
        form = BatchForm(request.POST, acting_employee=acting_employee, batch=batch)
        if form.is_valid():
            entries = _drop_skipped(form.entries, request.POST)
            found = rules.find_conflicts(entries, exclude_batch=batch)
            if not entries:
                form.add_error(None, "Every line was removed - nothing left to resubmit.")
            elif not found:
                c = form.cleaned_data
                if batch.status == OfficialRequest.RETURNED:
                    msg = "Group request resubmitted. It starts again from the first approval step."
                else:
                    msg = "Group request updated. It is still waiting at the first approval step."
                rules.resubmit_batch(batch, request.user, c["purpose"].strip(), (c.get("destination") or "").strip(),
                                     entries, request.POST.get("resubmit_note", "").strip(),
                                     c.get("expenses_authorized") or "", (c.get("transportation_allowed") or "").strip(),
                                     (c.get("past_date_reason") or "").strip())
                messages.success(request, msg)
                return redirect("official_requests:batch_detail", pk=pk)
            ctx = _form_context(request, form, acting_employee, _rows_from_post(request.POST),
                                request.POST.getlist("employees"), _conflict_rows(entries, found), batch)
        else:
            ctx = _form_context(request, form, acting_employee, _rows_from_post(request.POST),
                                request.POST.getlist("employees"), batch=batch)
    else:
        form = BatchForm(acting_employee=acting_employee, batch=batch,
                         initial={"kind": kind, "purpose": batch.purpose, "destination": batch.destination,
                                  "expenses_authorized": batch.expenses_authorized,
                                  "transportation_allowed": batch.transportation_allowed,
                                  "past_date_reason": batch.past_date_reason, "apply_same": False})
        selected = sorted({line.employee_id for line in batch.active_lines()})
        ctx = _form_context(request, form, acting_employee, _rows_from_batch(batch), selected, batch=batch)
    ctx["latest_return"] = latest_return
    return render(request, "official_requests/batch_form.html", ctx)


def can_view_batch(acting_employee, user, batch):
    if acting_employee is None:
        return False
    if batch.filed_by_id == user.pk or is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True) \
            or is_chief_of_hospital(acting_employee, True):
        return True
    lines = list(batch.lines.select_related("employee"))
    if any(line.employee_id == acting_employee.pk for line in lines):
        return True
    return any(is_supervisor_of(acting_employee, line.employee, viewing=True) for line in lines)


@login_required
def batch_detail(request, pk):
    acting_employee = get_acting_employee(request.user)
    batch = get_object_or_404(OfficialRequestBatch, pk=pk)
    if not can_view_batch(acting_employee, request.user, batch):
        raise PermissionDenied("You are not authorized to view this group request.")
    return render(request, "official_requests/batch_detail.html", {
        "batch": batch,
        "lines": batch.active_lines(),
        "removed": batch.lines.filter(status=OfficialRequest.CANCELLED).select_related("employee"),
        "history": batch.actions.select_related("acted_by__employee"),
        "actions": rules.available_actions(acting_employee, request.user, batch),
        "route": rules.route_labels(batch),
        "skipped": rules.skipped_steps(batch),
        "next_step": (rules.step_action(batch.request_type, rules.next_status(batch))[2]
                      if rules.next_status(batch) else None)
        if batch.status not in OfficialRequest.TERMINAL_STATUSES | {OfficialRequest.RETURNED} else None,
        "is_filer": batch.filed_by_id == request.user.pk,
        "can_edit_pending": rules.pending_untouched(batch) and batch.filed_by_id == request.user.pk,
    })


@login_required
def batch_action(request, pk):
    if request.method != "POST":
        return redirect("official_requests:batch_detail", pk=pk)
    acting_employee = get_acting_employee(request.user)
    batch = get_object_or_404(OfficialRequestBatch, pk=pk)
    action = request.POST.get("action")
    notes = request.POST.get("notes", "").strip()[:255]
    if action not in {code for code, _ in rules.available_actions(acting_employee, request.user, batch)}:
        raise PermissionDenied("You are not authorized to take this action on this group request.")
    if action in ("return", "reject") and not notes:
        messages.error(request, "Type the reason for returning or rejecting it, so the filer knows why.")
        return redirect("official_requests:batch_detail", pk=pk)
    rules.apply_action(batch, request.user, action, notes)
    messages.success(request, "Action recorded for the whole group request.")
    return redirect("official_requests:request_queue")


@login_required
def print_batch_travel_order(request, pk):
    """One Travel Order listing everyone on a group Official Business request."""
    from .models import OfficialRequest as R
    from .travel_order import data_for_batch, printable
    from .views import _travel_order_response

    acting_employee = get_acting_employee(request.user)
    batch = get_object_or_404(OfficialRequestBatch, pk=pk)
    if not can_view_batch(acting_employee, request.user, batch):
        raise PermissionDenied("You are not authorized to view this group request.")
    if batch.request_type != R.OFFICIAL_BUSINESS:
        raise PermissionDenied("Only Official Business (Travel Order) requests print a Travel Order.")
    if not printable(batch.status):
        messages.error(request, "The Travel Order prints once the Chief of Hospital has recommended approval.")
        return redirect("official_requests:batch_detail", pk=pk)
    return _travel_order_response(request, data_for_batch(batch), f"Travel-Order_group-{batch.pk}.pdf",
                                  "official_requests:request_queue")
