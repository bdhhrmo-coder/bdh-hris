"""
HR Tools -> Data Import. Two imports, same three steps each:
    1. upload the filled template
    2. preview: every row checked; nothing saved yet
    3. confirm: saved in one go (or not at all if anything is wrong)

The uploaded file is kept in the session between steps 2 and 3, and is
checked again at confirm time, so nothing that changed in between can slip
through. Temporary passwords for new accounts are also kept only in the
session (never stored in plain text in the database) until HR has printed
the slips and clears them.
"""

import base64
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import RoleAssignment
from employees.permissions import get_acting_employee
from leave.pdf_convert import workbook_to_pdf
from printouts.http import pdf_response

from . import balance_import, employee_import
from .common import SheetError
from .template import build_password_slips, build_template

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
KINDS = {
    "employees": (employee_import, "Employee list"),
    "balances": (balance_import, "Opening balances"),
}


def _require_importer(request):
    """HR Administrator or HR Processor (owner decision, 2026-10-06)."""
    employee = get_acting_employee(request.user)
    if employee is None or not (
        employee.has_role(RoleAssignment.HR_ADMINISTRATOR) or employee.has_role(RoleAssignment.HR_PROCESSOR)
    ):
        raise PermissionDenied("Only HR may run data imports.")
    return employee


def _plan_from_session(request, kind, actor_employee):
    stored = request.session.get(f"dataimport_{kind}")
    if not stored:
        return None, None
    data = io.BytesIO(base64.b64decode(stored["file"]))
    module = KINDS[kind][0]
    plans = module.build_plan(data, actor_employee) if kind == "employees" else module.build_plan(data)
    return plans, stored["name"]


@login_required
def home(request):
    _require_importer(request)
    return render(request, "dataimport/home.html", {"slip_count": len(request.session.get("dataimport_slips", []))})


@login_required
def download_template(request):
    _require_importer(request)
    buffer = io.BytesIO()
    build_template().save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="BDH-HRIS_Import_Template.xlsx"'
    return response


@login_required
@require_POST
def upload(request, kind):
    actor = _require_importer(request)
    if kind not in KINDS:
        raise PermissionDenied
    f = request.FILES.get("file")
    if f is None:
        messages.error(request, "Please choose the filled template file first.")
        return redirect("dataimport:home")
    if not f.name.lower().endswith(".xlsx") or f.size > MAX_UPLOAD_BYTES:
        messages.error(request, "Please upload the .xlsx template (5 MB at most).")
        return redirect("dataimport:home")
    data = f.read()
    request.session[f"dataimport_{kind}"] = {"file": base64.b64encode(data).decode(), "name": f.name}
    try:
        plans, _ = _plan_from_session(request, kind, actor)
    except SheetError as exc:
        request.session.pop(f"dataimport_{kind}", None)
        messages.error(request, str(exc))
        return redirect("dataimport:home")
    return _preview(request, kind, plans, f.name)


def _preview(request, kind, plans, name):
    errors = sum(1 for p in plans if p.errors)
    return render(request, "dataimport/preview.html", {
        "kind": kind, "title": KINDS[kind][1], "file_name": name, "plans": plans,
        "error_rows": errors,
        "new_count": sum(1 for p in plans if p.action == "new"),
        "update_count": sum(1 for p in plans if p.action == "update"),
        "same_count": sum(1 for p in plans if p.action == "no change"),
    })


@login_required
@require_POST
def confirm(request, kind):
    actor = _require_importer(request)
    if kind not in KINDS:
        raise PermissionDenied
    try:
        plans, name = _plan_from_session(request, kind, actor)
    except SheetError as exc:
        messages.error(request, str(exc))
        return redirect("dataimport:home")
    if plans is None:
        messages.error(request, "Please upload the file again.")
        return redirect("dataimport:home")
    if any(p.errors for p in plans):
        messages.error(request, "Some rows have errors, so nothing was saved. Fix the file and upload it again.")
        return _preview(request, kind, plans, name)

    request.session.pop(f"dataimport_{kind}", None)
    if kind == "employees":
        created, updated, credentials = employee_import.apply_plan(plans, request.user, name)
        if credentials:
            request.session["dataimport_slips"] = request.session.get("dataimport_slips", []) + credentials
        messages.success(
            request,
            f"Saved: {created} new employee(s), {updated} updated. "
            + (f"{len(credentials)} new login(s) - print the password slips below." if credentials else ""),
        )
    else:
        added = balance_import.apply_plan(plans, request.user, name)
        messages.success(request, f"Saved: {added} opening-balance adjustment(s).")
    return redirect("dataimport:home")


@login_required
def password_slips(request, fmt):
    _require_importer(request)
    credentials = request.session.get("dataimport_slips", [])
    if not credentials:
        messages.info(request, "There are no password slips waiting.")
        return redirect("dataimport:home")
    wb = build_password_slips(credentials, request.build_absolute_uri(reverse("login")))
    if fmt == "pdf":
        return pdf_response(request, lambda: workbook_to_pdf(wb, "Password slips"),
                            "BDH-HRIS_Password_Slips.pdf", "dataimport:home")
    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="BDH-HRIS_Password_Slips.xlsx"'
    return response


@login_required
@require_POST
def clear_slips(request):
    _require_importer(request)
    request.session.pop("dataimport_slips", None)
    messages.success(request, "Password slips cleared from the system.")
    return redirect("dataimport:home")
