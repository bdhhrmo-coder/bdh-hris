"""Screens for the holiday list and the assignment history (rebuilt 2026-11-26)."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from orgstructure.models import Section, Unit

from . import history as H
from .holidays import add_holiday, void_holiday, year_has_holidays
from .models import AssignmentLine, Employee, Holiday, PersonnelActionType
from .permissions import get_acting_employee


def _date(raw):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise ValidationError("Enter a valid date.")


@login_required
def holidays(request):
    acting = get_acting_employee(request.user)
    can_edit = H.is_hr_admin(acting)  # v1.1 §7.1: everyone views, the HR Administrator keeps it
    if request.method == "POST" and not can_edit:
        raise PermissionDenied("The holiday list is kept by the HR Administrator.")
    try:
        year = int(request.GET.get("year") or date.today().year)
    except ValueError:
        year = date.today().year
    if request.method == "POST":
        try:
            if request.POST.get("what") == "void":
                void_holiday(request.user, get_object_or_404(Holiday, pk=request.POST.get("pk")),
                             request.POST.get("reason", ""))
                messages.success(request, "Holiday voided.")
            else:
                h = add_holiday(request.user, _date(request.POST.get("date")), request.POST.get("name"),
                                request.POST.get("kind") or Holiday.REGULAR, request.POST.get("basis_ref", ""))
                messages.success(request, f"{h} added.")
                year = h.date.year
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{request.path}?year={year}")
    rows = Holiday.objects.filter(date__year=year).select_related("created_by__employee", "voided_by__employee")
    if not can_edit:
        rows = rows.filter(voided_at__isnull=True)
    return render(request, "employees/holidays.html", {
        "rows": rows, "year": year, "years": range(date.today().year - 1, date.today().year + 3),
        "kinds": Holiday.KIND_CHOICES, "next_year_empty": not year_has_holidays(date.today().year + 1),
        "can_edit": can_edit})


@login_required
def assignments(request):
    acting = get_acting_employee(request.user)
    if not H.is_hr(acting):
        raise PermissionDenied("Assignment history is kept by HR.")
    if request.method == "POST":
        what = request.POST.get("what")
        try:
            if what == "opening":
                n = H.load_opening_lines(acting, request.user, _date(request.POST.get("as_of")))
                messages.success(request, f"{n} opening line(s) loaded.")
            elif what == "type_add":
                if not H.is_hr_admin(acting):
                    raise ValidationError("Only an HR Administrator keeps the list of action types.")
                name = " ".join(request.POST.get("name", "").split())
                if not name or PersonnelActionType.objects.filter(name__iexact=name).exists():
                    raise ValidationError("Type a new name that is not on the list yet.")
                PersonnelActionType.objects.create(name=name, sort_order=999)
                messages.success(request, f"{name} added.")
            elif what == "type_toggle":
                if not H.is_hr_admin(acting):
                    raise ValidationError("Only an HR Administrator keeps the list of action types.")
                t = get_object_or_404(PersonnelActionType, pk=request.POST.get("pk"))
                t.is_active = not t.is_active
                t.save(update_fields=["is_active"])
                messages.success(request, f"{t} switched {'on' if t.is_active else 'off'}.")
            elif what in ("verify", "reject", "close_verify", "close_reject"):
                line = get_object_or_404(AssignmentLine, pk=request.POST.get("pk"))
                note = request.POST.get("notes", "")
                if what.startswith("close"):
                    H.verify_close(acting, request.user, line, what == "close_verify", note)
                else:
                    H.verify(acting, request.user, line, what == "verify", note)
                messages.success(request, "Recorded.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("employees:assignments")
    q = request.GET.get("q", "").strip()
    found = []
    if q:
        from django.db.models import Q
        found = Employee.objects.filter(Q(surname__icontains=q) | Q(first_name__icontains=q)
                                        | Q(employee_id__icontains=q))[:30]
    return render(request, "employees/assignments.html", {
        "q": q, "found": found, "waiting": H.waiting_for(acting), "is_admin": H.is_hr_admin(acting),
        "types": PersonnelActionType.objects.all()})


def may_view_history(acting, employee):
    """v1.1 §7.1: the employee (own), their supervisor (own staff), HR, the AO and the COH."""
    from accounts.models import RoleAssignment as R
    from leave.permissions import endorsers_for

    if acting is None:
        return False
    if acting.pk == employee.pk or H.is_hr(acting):
        return True
    if acting.has_role(R.ADMINISTRATIVE_OFFICER, viewing=True) or acting.has_role(R.CHIEF_OF_HOSPITAL, viewing=True):
        return True
    return acting in endorsers_for([employee])


@login_required
def employee_assignments(request, pk):
    acting = get_acting_employee(request.user)
    employee = get_object_or_404(Employee, pk=pk)
    if not may_view_history(acting, employee):
        raise PermissionDenied("You may see only your own assignment history.")
    can_edit = H.is_hr(acting) and acting.pk != employee.pk
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("You cannot change this record.")
        try:
            if request.POST.get("what") == "close":
                line = get_object_or_404(AssignmentLine, pk=request.POST.get("pk"), employee=employee)
                H.request_close(acting, request.user, line, _date(request.POST.get("close_on")),
                                request.POST.get("reason", ""))
                messages.success(request, "Closing sent for verification.")
            else:
                section = Section.objects.filter(pk=request.POST.get("section") or 0).first()
                unit = Unit.objects.filter(pk=request.POST.get("unit") or 0).first()
                kind = get_object_or_404(PersonnelActionType, pk=request.POST.get("action_type") or 0)
                H.encode(acting, request.user, employee, section, unit, kind,
                         _date(request.POST.get("effective_from")), request.POST.get("position", ""),
                         request.POST.get("reference", ""), request.POST.get("remarks", ""))
                messages.success(request, "Line encoded. Another HR Administrator verifies it.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("employees:employee_assignments", pk=pk)
    lines = employee.assignment_lines.select_related("section", "unit", "action_type", "encoded_by__employee",
                                                      "verified_by__employee").prefetch_related("actions")
    return render(request, "employees/employee_assignments.html", {
        "employee": employee, "lines": lines, "can_edit": can_edit,
        "sections": Section.objects.filter(is_active=True), "units": Unit.objects.filter(is_active=True),
        "types": PersonnelActionType.objects.filter(is_active=True), "today": date.today()})
