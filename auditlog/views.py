from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from employees.models import Employee
from employees.permissions import get_acting_employee

from . import retention
from .aggregation import MODULE_CHOICES, audit_rows
from .models import RetentionReviewRecord
from .permissions import can_view_audit_log


def _require_audit_access(request):
    acting_employee = get_acting_employee(request.user)
    if not can_view_audit_log(acting_employee):
        raise PermissionDenied("The audit log is available to HR, AO, COH, and System Administrator roles.")
    return acting_employee


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


@login_required
def audit_log_view(request):
    _require_audit_access(request)

    employee_id = request.GET.get("employee") or None
    module = request.GET.get("module") or None
    date_from = _parse_date(request.GET.get("date_from"))
    date_to = _parse_date(request.GET.get("date_to"))

    rows = audit_rows(
        employee_id=int(employee_id) if employee_id else None,
        module=module,
        date_from=date_from,
        date_to=date_to,
    )

    return render(
        request,
        "auditlog/audit_log.html",
        {
            "rows": rows[:500],
            "row_count": len(rows),
            "truncated": len(rows) > 500,
            "modules": MODULE_CHOICES,
            "employees": Employee.objects.all(),  # default ordering: Surname, First Name, Middle Name
            "selected_employee_id": employee_id,
            "selected_module": module,
            "date_from": request.GET.get("date_from", ""),
            "date_to": request.GET.get("date_to", ""),
        },
    )


@login_required
def retention_review_list(request):
    _require_audit_access(request)

    return render(
        request,
        "auditlog/retention_review_list.html",
        {
            "due": retention.employees_due_for_retention_review(),
            "history": retention.retention_review_history(),
            "cutoff_date": retention.retention_cutoff_date(),
            "decision_choices": RetentionReviewRecord.DECISION_CHOICES,
        },
    )


@login_required
def retention_review_decide(request, employee_pk):
    acting_employee = _require_audit_access(request)
    employee = get_object_or_404(Employee, pk=employee_pk)

    if request.method == "POST":
        decision = request.POST.get("decision")
        reason = (request.POST.get("reason") or "").strip()
        valid_decisions = {code for code, _ in RetentionReviewRecord.DECISION_CHOICES}

        if decision not in valid_decisions:
            messages.error(request, "Choose a valid decision (Retain or Dispose).")
        elif not reason:
            messages.error(request, "A reason is required to record a retention decision.")
        else:
            RetentionReviewRecord.objects.create(
                employee=employee,
                decision=decision,
                reason=reason,
                reviewed_by=request.user,
            )
            messages.success(request, f"Retention decision recorded for {employee}.")
            return redirect("auditlog:retention_review_list")

    return redirect("auditlog:retention_review_list")
