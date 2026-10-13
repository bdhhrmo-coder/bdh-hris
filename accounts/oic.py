"""
Officer-in-Charge (OIC) screen - Batch 7 (owner, 2026-10-13).

CLAUDE.md Section 3: OIC coverage is ad-hoc delegation triggered by the
approver themself. Before this screen existed it could only be entered in
Django admin. Rules (Phase 1 decisions, unchanged):

  - Who may designate: anyone holding their own (non-OIC) Supervisor,
    Administrative Officer or Chief of Hospital assignment, for that same
    role and area only.
  - Who may be OIC: an employee who already holds an active Supervisor
    assignment (not the delegator).
  - The OIC acts only between the start and end dates; while it is in
    force the delegator cannot act in that area (Employee.active_role_assignments).
  - "End now" stops it early; nothing is ever deleted.

The system records and enforces the designation; choosing the OIC stays the
approver's decision.
"""

from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from employees.models import Employee
from employees.permissions import get_acting_employee
from notifications.services import notify

from .models import RoleAssignment as R


def own_delegable_assignments(employee):
    """The delegator's own assignments that can be handed to an OIC.

    Reads the table directly, not active_role_assignments(): during an
    OIC period that method sets these aside, and the delegator must still
    be able to open this screen and end the OIC early."""
    if employee is None:
        return R.objects.none()
    return (
        R.objects.filter(R.in_force_q(), employee=employee, is_oic=False, role__in=R.OIC_ELIGIBLE_ROLES)
        .select_related("section", "unit")
        .order_by("role", "section__name", "unit__name")
    )


def can_designate_oic(employee):
    return own_delegable_assignments(employee).exists()


def oic_candidates(delegator):
    supervisor_ids = R.objects.filter(R.in_force_q(), role=R.SUPERVISOR, is_oic=False).values_list(
        "employee_id", flat=True
    )
    return (
        Employee.objects.filter(pk__in=supervisor_ids, is_active=True, archived_at__isnull=True)
        .exclude(pk=delegator.pk)
        .order_by("surname", "first_name")
    )


class OICForm(forms.Form):
    scope = forms.ModelChoiceField(
        queryset=R.objects.none(), label="Area to hand over", empty_label=None,
        help_text="Your own role and area. The OIC acts only here.",
    )
    oic = forms.ModelChoiceField(
        queryset=Employee.objects.none(), label="Officer-in-Charge",
        help_text="Only employees who are already Supervisors can be designated.",
    )
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="The OIC stops on its own after this date. You can end it earlier.",
    )

    def __init__(self, *args, delegator, **kwargs):
        super().__init__(*args, **kwargs)
        self.delegator = delegator
        self.fields["scope"].queryset = own_delegable_assignments(delegator)
        self.fields["scope"].label_from_instance = lambda ra: ra.scope_label
        self.fields["oic"].queryset = oic_candidates(delegator)
        self.fields["oic"].label_from_instance = lambda e: f"{e.full_name} ({e.employee_id})"
        if not self.is_bound:
            self.initial.setdefault("start_date", timezone.localdate())

    def clean(self):
        cleaned = super().clean()
        start, end, scope = cleaned.get("start_date"), cleaned.get("end_date"), cleaned.get("scope")
        today = timezone.localdate()
        if start and start < today:
            self.add_error("start_date", "The start date cannot be in the past.")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the start date.")
        if start and end and scope:
            overlap = R.objects.filter(
                is_oic=True, is_active=True, delegated_by=self.delegator, role=scope.role,
                section_id=scope.section_id, unit_id=scope.unit_id,
                start_date__lte=end,
            ).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
            if overlap.exists():
                raise forms.ValidationError(
                    "You already have an OIC for this area on some of these dates. End it first."
                )
        return cleaned


def _status(ra, today):
    if not ra.is_active:
        return "Cancelled"
    if ra.start_date and ra.start_date > today:
        return "Upcoming"
    if ra.end_date and ra.end_date < today:
        return "Ended"
    return "In force"


@login_required
def oic_home(request):
    acting = get_acting_employee(request.user)
    if not can_designate_oic(acting):
        raise PermissionDenied("Only Supervisors, the Administrative Officer and the Chief of Hospital can designate an OIC.")

    if request.method == "POST":
        form = OICForm(request.POST, delegator=acting)
        if form.is_valid():
            scope = form.cleaned_data["scope"]
            ra = R(
                employee=form.cleaned_data["oic"], role=scope.role, section=scope.section, unit=scope.unit,
                is_oic=True, delegated_by=acting, start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"], created_by=request.user,
            )
            ra.full_clean()
            ra.save()
            notify(
                ra.employee,
                f"{acting.full_name} designated you OIC ({scope.scope_label}) from "
                f"{ra.start_date:%b %d, %Y} to {ra.end_date:%b %d, %Y}.",
                url="/hris/home/",
            )
            messages.success(
                request, f"{ra.employee.full_name} is OIC from {ra.start_date:%b %d} to {ra.end_date:%b %d}. "
                "During those dates you cannot act on this area."
            )
            return redirect("oic_home")
    else:
        form = OICForm(delegator=acting)

    today = timezone.localdate()
    made = (
        R.objects.filter(is_oic=True, delegated_by=acting)
        .select_related("employee", "section", "unit").order_by("-start_date", "-pk")[:30]
    )
    received = (
        R.objects.filter(R.in_force_q(), is_oic=True, employee=acting)
        .select_related("delegated_by", "section", "unit").order_by("start_date")
    )
    rows = [{"ra": ra, "status": _status(ra, today)} for ra in made]
    return render(request, "accounts/oic.html", {"form": form, "rows": rows, "received": received})


@login_required
@require_POST
def oic_end(request, pk):
    acting = get_acting_employee(request.user)
    ra = get_object_or_404(R, pk=pk, is_oic=True)
    if acting is None or ra.delegated_by_id != acting.pk:
        raise PermissionDenied("Only the person who designated this OIC can end it.")
    today = timezone.localdate()
    status = _status(ra, today)
    if status not in ("In force", "Upcoming"):
        messages.info(request, "This OIC designation has already ended.")
        return redirect("oic_home")
    if status == "Upcoming" or ra.start_date == today:
        ra.is_active = False  # never started (or started today): cancel it
    else:
        ra.end_date = today - timedelta(days=1)  # last day was yesterday; you act again today
    ra.ended_by = request.user
    ra.ended_at = timezone.now()
    ra.save(update_fields=["is_active", "end_date", "ended_by", "ended_at", "updated_at"])
    notify(ra.employee, f"Your OIC designation ({ra.scope_label}) from {acting.full_name} has ended.",
           url="/hris/home/")
    messages.success(request, "OIC ended. You can act on this area again.")
    return redirect("oic_home")
