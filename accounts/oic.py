"""
Officer-in-Charge (OIC) screen - Batch 7 (owner, 2026-10-13).

CLAUDE.md Section 3: OIC coverage is ad-hoc delegation triggered by the
approver themself. Before this screen existed it could only be entered in
Django admin. Rules (Phase 1 decisions, unchanged):

  - Who may designate: anyone holding their own (non-OIC) Supervisor,
    Administrative Officer or Chief of Hospital assignment, for that same
    role and area only.
  - Who may be OIC: an employee who already holds an active Supervisor
    assignment (not the delegator). A COSP employee may also be OIC, but
    only for a Supervisor area at unit level, or of a section that has no
    units, and only if they belong to that unit/section - never for the
    Chief Nurse (the section head above unit heads), the AO or the COH
    (owner decision 2026-11-03, dry-run item 11).
  - The OIC acts only between the start and end dates; while it is in
    force the delegator cannot act in that area (Employee.active_role_assignments).
  - "End now" stops it early; nothing is ever deleted.
  - HR Administrator may designate on an approver's behalf (accounts/oic_hr.py,
    owner decision 2026-11-16, Option B): reason and written instruction
    required, shown in the Audit Log. The System Administrator cannot.

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


def _supervisor_ids():
    return set(R.objects.filter(R.in_force_q(), role=R.SUPERVISOR, is_oic=False).values_list(
        "employee_id", flat=True))


def cosp_may_cover(scope):
    """A COSP OIC is allowed for a Supervisor area at unit level, or for a
    section that has no units (so never the Chief Nurse, AO or COH)."""
    if scope is None or scope.role != R.SUPERVISOR:
        return False
    if scope.unit_id:
        return True
    return bool(scope.section_id) and not scope.section.units.exists()


def _cosp_in_area(employee, scope):
    if employee.employment_status != Employee.EMPLOYMENT_STATUS_COSP or not cosp_may_cover(scope):
        return False
    if scope.unit_id:
        return employee.units.filter(pk=scope.unit_id).exists()
    return employee.sections.filter(pk=scope.section_id).exists()


def _ao_ids():
    return set(R.objects.filter(R.in_force_q(), role=R.ADMINISTRATIVE_OFFICER, is_oic=False).values_list(
        "employee_id", flat=True))


def may_be_oic(person, scope):
    """Pilot Item 5 (owner decisions 2026-11-24): Supervisors (unit heads, section heads incl.
    the Chief Nurse) may be OIC for any approver area, the AO also for the COH; a COSP employee
    only within the limits of cosp_may_cover (never for the Chief Nurse, the AO or the COH)."""
    if person is None or scope is None or person.pk == scope.employee_id:
        return False
    cosp = person.employment_status == Employee.EMPLOYMENT_STATUS_COSP
    if cosp and not cosp_may_cover(scope):
        return False
    if person.pk in _supervisor_ids():
        return True
    if scope.role == R.CHIEF_OF_HOSPITAL and person.pk in _ao_ids():
        return True
    return _cosp_in_area(person, scope)


def oic_candidates(delegator):
    ids = _supervisor_ids()
    if own_delegable_assignments(delegator).filter(role=R.CHIEF_OF_HOSPITAL).exists():
        ids |= _ao_ids()  # Item 5: the COH may name the AO
    cosp_scopes = [ra for ra in own_delegable_assignments(delegator) if cosp_may_cover(ra)]
    if cosp_scopes:
        cosp = Employee.objects.filter(employment_status=Employee.EMPLOYMENT_STATUS_COSP, is_active=True)
        ids |= {e.pk for e in cosp if any(_cosp_in_area(e, ra) for ra in cosp_scopes)}
    return (
        Employee.objects.filter(pk__in=ids, is_active=True, archived_at__isnull=True)
        .exclude(pk=delegator.pk)
        .order_by("surname", "first_name")
    )


OIC_NOT_ALLOWED = ("This person cannot be OIC for this area. A Supervisor (unit head or section head) can be OIC "
                   "for any approver; the AO also for the COH. A COSP employee only for their own unit (or their "
                   "own section when it has no units) - never for the Chief Nurse, the AO or the COH.")


class OICForm(forms.Form):
    scope = forms.ModelChoiceField(
        queryset=R.objects.none(), label="Area to hand over", empty_label=None,
        help_text="Your own role and area. The OIC acts only here.",
    )
    oic = forms.ModelChoiceField(
        queryset=Employee.objects.none(), label="Officer-in-Charge",
        help_text="Supervisors (unit heads and section heads); for the COH, also the AO. For a unit (or a "
                  "section without units), a COSP employee of that unit/section may also be designated.",
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
        oic = cleaned.get("oic")
        if oic and scope and not may_be_oic(oic, scope):
            self.add_error("oic", OIC_NOT_ALLOWED)
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
    # The approver ends their own OIC. An HR Administrator may also end one that
    # HR designated on the approver's behalf (owner decision 2026-11-16).
    hr_made = ra.designated_by_hr_id is not None and acting is not None and acting.is_hr_administrator()
    if acting is None or (ra.delegated_by_id != acting.pk and not hr_made):
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
    notify(ra.employee, f"Your OIC designation ({ra.scope_label}) from {ra.delegated_by.full_name} has ended.",
           url="/hris/home/")
    if hr_made and ra.delegated_by_id != acting.pk:
        notify(ra.delegated_by, f"HR ({acting.full_name}) ended the OIC for your area ({ra.scope_label}). "
                                "You can act on it again.", url="/oic/")
        messages.success(request, "OIC ended. The approver can act on this area again.")
        return redirect("oic_hr_home")
    messages.success(request, "OIC ended. You can act on this area again.")
    return redirect("oic_home")
