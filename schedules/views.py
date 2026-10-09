from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import RoleAssignment
from employees.models import Employee
from employees.permissions import get_acting_employee
from leave.permissions import is_administrative_officer, is_chief_of_hospital, is_hr
from orgstructure.models import Section, Unit
from printouts.http import pdf_response

from . import rules, services
from .forms import NewScheduleForm, ShiftCodeForm
from .models import DutySchedule, ScheduleCell, ScheduleRow, ShiftCode


def _acting(request):
    acting = get_acting_employee(request.user)
    if acting is None:
        raise PermissionDenied("No employee record is linked to your account.")
    return acting


def _get(request, pk):
    acting = _acting(request)
    schedule = get_object_or_404(DutySchedule.objects.select_related("section", "unit", "prepared_by"), pk=pk)
    if not rules.can_view(acting, request.user, schedule):
        raise PermissionDenied("You are not allowed to see this schedule.")
    return acting, schedule


def _grid(schedule):
    """Rows with their cells (one per day) and totals, ready for templates."""
    rows = list(schedule.rows.select_related("employee"))
    cells = {}
    for c in ScheduleCell.objects.filter(row__schedule=schedule).select_related("shift"):
        cells.setdefault(c.row_id, {})[c.date] = c
    out = []
    for r in rows:
        row_cells = [cells.get(r.pk, {}).get(d) for d in schedule.days]
        hours, days = rules.row_totals([c for c in row_cells if c])
        out.append({"row": r, "cells": row_cells, "hours": hours, "days": days})
    return out


def _areas_for(acting):
    """Sections/units this person may prepare a schedule for."""
    sections = Section.objects.filter(is_active=True).order_by("name")
    units = Unit.objects.filter(is_active=True).select_related("section").order_by("section__name", "name")
    if is_hr(acting):
        return list(sections), list(units)
    return ([s for s in sections if rules.supervises_area(acting, section=s)],
            [u for u in units if rules.supervises_area(acting, unit=u)])


# -- lists -------------------------------------------------------------------------

@login_required
def schedule_list(request):
    acting = _acting(request)
    see_all = is_hr(acting) or is_administrative_officer(acting) or is_chief_of_hospital(acting)
    qs = DutySchedule.objects.select_related("section", "unit")
    schedules = [s for s in qs if see_all or rules.can_view(acting, request.user, s)]
    sections, units = _areas_for(acting)
    return render(request, "schedules/list.html", {
        "schedules": schedules, "can_create": bool(sections or units),
        "waiting": rules.schedules_waiting_for(acting, request.user),
        "can_manage_codes": acting.has_role(RoleAssignment.HR_ADMINISTRATOR),
    })


@login_required
def my_schedule(request):
    acting = _acting(request)
    today = timezone.localdate()
    try:
        year, month = int(request.GET.get("y", today.year)), int(request.GET.get("m", today.month))
        first = date(year, month, 1)
    except ValueError:
        first = today.replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    rows = ScheduleRow.objects.filter(employee=acting, schedule__year=first.year, schedule__month=first.month,
                                      schedule__status=DutySchedule.RECORDED).select_related("schedule__section",
                                                                                             "schedule__unit")
    blocks = []
    for r in rows:
        cells = list(r.cells.select_related("shift"))
        hours, days = rules.row_totals(cells)
        blocks.append({"schedule": r.schedule, "cells": cells, "hours": hours, "days": days})
    return render(request, "schedules/my_schedule.html", {"first": first, "prev": prev, "next": nxt, "blocks": blocks})


# -- create / edit -----------------------------------------------------------------

@login_required
def schedule_new(request):
    acting = _acting(request)
    sections, units = _areas_for(acting)
    if not (sections or units):
        raise PermissionDenied("Only a section/unit Supervisor or HR can prepare a duty schedule.")
    form = NewScheduleForm(request.POST or None, sections=sections, units=units)
    if request.method == "POST" and form.is_valid():
        section, unit = form.chosen_area()
        year, month = form.cleaned_data["year"], form.cleaned_data["month"]
        existing = DutySchedule.objects.filter(section=section if unit is None else None, unit=unit,
                                               year=year, month=month).first()
        if existing:
            messages.info(request, "A schedule for that month already exists - opened it.")
            return redirect("schedules:detail", pk=existing.pk)
        role = rules.preparer_role(acting, section, unit)
        schedule = services.create_schedule(request.user, role, year, month,
                                            section=section if unit is None else None, unit=unit)
        return redirect("schedules:edit", pk=schedule.pk)
    return render(request, "schedules/new.html", {"form": form})


@login_required
def schedule_edit(request, pk):
    acting, schedule = _get(request, pk)
    if not rules.can_edit(acting, request.user, schedule):
        messages.error(request, "This schedule can't be edited now.")
        return redirect("schedules:detail", pk=pk)
    if request.method == "POST":
        values = {}
        row_ids = set(schedule.rows.values_list("pk", flat=True))
        for key, val in request.POST.items():
            if not key.startswith("c-"):
                continue
            try:
                _, row_id, day = key.split("-")
                row_id, day = int(row_id), int(day)
                values[(row_id, date(schedule.year, schedule.month, day))] = int(val) if val else None
            except (ValueError, TypeError):
                continue
            if row_id not in row_ids:
                raise PermissionDenied("Unknown row.")
        services.save_grid(schedule, values)
        schedule.notes = request.POST.get("notes", schedule.notes)[:2000]
        fields = ["notes", "updated_at"]
        if is_hr(acting) and request.POST.get("cutoff_date"):
            try:
                schedule.cutoff_date = date.fromisoformat(request.POST["cutoff_date"])
                fields.append("cutoff_date")
            except ValueError:
                messages.error(request, "The cut-off date was not understood; it was not changed.")
        schedule.save(update_fields=fields)
        add_id = request.POST.get("add_employee")
        if add_id:
            services.add_row(schedule, get_object_or_404(Employee, pk=add_id, is_active=True))
        remove_id = request.POST.get("remove_row")
        if remove_id:
            schedule.rows.filter(pk=remove_id).delete()
        if request.POST.get("then") == "submit":
            try:
                services.submit(schedule, request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return redirect("schedules:edit", pk=pk)
            messages.success(request, "Schedule submitted for HR review.")
            return redirect("schedules:detail", pk=pk)
        messages.success(request, "Schedule saved.")
        return redirect("schedules:edit", pk=pk)

    codes = list(ShiftCode.objects.filter(is_active=True))
    grid = _grid(schedule)
    editor = {
        "codes": [{"id": c.pk, "code": c.code, "hours": float(c.paid_hours), "duty": c.is_duty} for c in codes],
        "days": [{"d": d.day, "wd": d.strftime("%a")[:2]} for d in schedule.days],
        "rows": [{"id": g["row"].pk, "name": g["row"].employee.full_name, "designation": g["row"].designation,
                  "cells": [c.shift_id if c and c.shift_id else "" for c in g["cells"]]} for g in grid],
    }
    in_schedule = set(schedule.rows.values_list("employee_id", flat=True))
    return render(request, "schedules/edit.html", {
        "schedule": schedule, "editor": editor, "can_set_cutoff": is_hr(acting),
        "addable": Employee.objects.filter(is_active=True, archived_at__isnull=True).exclude(pk__in=in_schedule),
        "conflicts": services.double_bookings(schedule), "latest_return": schedule.actions.filter(action="return").last(),
    })


# -- view / act ---------------------------------------------------------------------

@login_required
def schedule_detail(request, pk):
    acting, schedule = _get(request, pk)
    return render(request, "schedules/detail.html", {
        "schedule": schedule, "grid": _grid(schedule), "history": schedule.actions.select_related("acted_by__employee"),
        "changes": schedule.changes.select_related("employee", "changed_by__employee"),
        "actions": rules.available_actions(acting, request.user, schedule),
        "can_edit": rules.can_edit(acting, request.user, schedule),
        "can_correct": rules.can_correct(acting, schedule),
        "codes": ShiftCode.objects.filter(is_active=True),
        "conflicts": services.double_bookings(schedule),
    })


@login_required
@require_POST
def schedule_action(request, pk):
    acting, schedule = _get(request, pk)
    action = request.POST.get("action")
    if action not in {code for code, _ in rules.available_actions(acting, request.user, schedule)}:
        raise PermissionDenied("You can't take this action on this schedule.")
    try:
        services.act(schedule, request.user, action, request.POST.get("notes", "").strip()[:255])
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("schedules:detail", pk=pk)
    messages.success(request, "Action recorded.")
    return redirect("schedules:list")


@login_required
@require_POST
def schedule_correct(request, pk):
    acting, schedule = _get(request, pk)
    if not rules.can_correct(acting, schedule):
        raise PermissionDenied("Only HR can correct an approved schedule.")
    row = get_object_or_404(ScheduleRow, pk=request.POST.get("row"), schedule=schedule)
    try:
        day = date.fromisoformat(request.POST.get("date", ""))
        shift_id = request.POST.get("shift")
        shift = ShiftCode.objects.get(pk=shift_id) if shift_id else None
        if day not in schedule.days:
            raise ValidationError("That date is not in this schedule's month.")
        services.hr_correction(schedule, row.employee, day, shift, request.POST.get("reason", ""), request.user)
    except (ValueError, ShiftCode.DoesNotExist):
        messages.error(request, "Choose a valid date and shift.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Correction saved and logged.")
    return redirect("schedules:detail", pk=pk)


# -- Excel and print --------------------------------------------------------------------

@login_required
def excel_template(request, pk):
    from .excel import build_template

    acting, schedule = _get(request, pk)
    wb = build_template(schedule)
    resp = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="Schedule_{schedule.area_name}_{schedule.year}-{schedule.month:02d}.xlsx"'
    wb.save(resp)
    return resp


@login_required
@require_POST
def excel_import(request, pk):
    from .excel import import_workbook

    acting, schedule = _get(request, pk)
    if not rules.can_edit(acting, request.user, schedule):
        raise PermissionDenied("This schedule can't be edited now.")
    upload = request.FILES.get("file")
    if not upload or not upload.name.lower().endswith((".xlsx", ".xlsm")):
        messages.error(request, "Choose the filled Excel template (.xlsx).")
        return redirect("schedules:edit", pk=pk)
    try:
        filled, problems = import_workbook(schedule, upload)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("schedules:edit", pk=pk)
    if problems:
        messages.warning(request, "Imported with notes: " + "; ".join(problems[:8]) + (" …" if len(problems) > 8 else ""))
    messages.success(request, f"Imported {filled} cell(s). Check the grid, then save or submit.")
    return redirect("schedules:edit", pk=pk)


@login_required
def schedule_print(request, pk):
    from .form_f50 import render_pdf

    acting, schedule = _get(request, pk)
    return pdf_response(request, lambda: render_pdf(schedule, printed_by=request.user),
                        f"Personnel-Work-Schedule_{schedule.area_name}_{schedule.year}-{schedule.month:02d}.pdf",
                        "schedules:list")


# -- shift codes (HR Administrator) -------------------------------------------------------

def _require_hr_admin(request):
    acting = _acting(request)
    if not acting.has_role(RoleAssignment.HR_ADMINISTRATOR):
        raise PermissionDenied("Only the HR Administrator maintains shift codes.")
    return acting


@login_required
def shift_codes(request):
    _require_hr_admin(request)
    return render(request, "schedules/shift_codes.html", {"codes": ShiftCode.objects.all()})


@login_required
def shift_code_edit(request, pk=None):
    _require_hr_admin(request)
    code = get_object_or_404(ShiftCode, pk=pk) if pk else None
    form = ShiftCodeForm(request.POST or None, instance=code)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Shift code saved.")
        return redirect("schedules:shift_codes")
    return render(request, "schedules/shift_code_form.html", {"form": form, "code": code})
