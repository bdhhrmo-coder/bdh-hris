"""
HR Administrator designates an OIC for an approver (owner decision 2026-11-16,
Option B). Only for an approver who cannot do it themself, so a reason and the
written instruction (PDF/JPG/PNG, e.g. the COH's memo or message) are required.
The rules for who may be OIC are exactly those of the approver's own screen
(accounts/oic.py). The System Administrator cannot use this.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from documents.validators import validate_upload
from employees.permissions import get_acting_employee
from notifications.services import notify

from .models import RoleAssignment as R
from .oic import OIC_NOT_ALLOWED, _status, may_be_oic


def can_hr_designate_oic(employee):
    return employee is not None and employee.is_hr_administrator()


def hr_delegable_assignments(acting):
    """Every approver's own (non-OIC) Supervisor / AO / COH line, except the
    HR Administrator's own: they use their own OIC screen for those."""
    qs = R.objects.filter(R.in_force_q(), is_oic=False, role__in=R.OIC_ELIGIBLE_ROLES,
                          employee__is_active=True, employee__archived_at__isnull=True)
    if acting is not None:
        qs = qs.exclude(employee=acting)
    return qs.select_related("employee", "section", "unit").order_by("role", "employee__surname", "section__name")


class HROICForm(forms.Form):
    scope = forms.ModelChoiceField(queryset=R.objects.none(), label="Approver and area", empty_label="Choose...")
    oic = forms.ModelChoiceField(queryset=R.objects.none(), label="Officer-in-Charge")
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reference = forms.CharField(
        max_length=100, label="Reference no. of the signed designation",
        help_text="The number of the signed Office Order, memo or designation (required).",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), label="Why the approver cannot designate",
        help_text="For example: on emergency leave, unreachable, hospitalised.",
    )
    instruction = forms.FileField(
        label="Written instruction",
        help_text="PDF, JPG or PNG, up to 10 MB: the approver's memo, message or order asking for this OIC.",
    )

    def __init__(self, *args, acting, **kwargs):
        super().__init__(*args, **kwargs)
        from employees.models import Employee

        self.acting = acting
        self.fields["scope"].queryset = hr_delegable_assignments(acting)
        self.fields["scope"].label_from_instance = lambda ra: f"{ra.employee.full_name} - {ra.scope_label}"
        self.fields["oic"].queryset = Employee.objects.filter(is_active=True, archived_at__isnull=True)
        self.fields["oic"].label_from_instance = lambda e: f"{e.full_name} ({e.employee_id})"
        if not self.is_bound:
            self.initial.setdefault("start_date", timezone.localdate())

    def clean(self):
        cleaned = super().clean()
        scope, oic = cleaned.get("scope"), cleaned.get("oic")
        if "reference" in cleaned and not cleaned["reference"].strip():
            self.add_error("reference", "Type the reference number of the signed designation.")
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and start < timezone.localdate():
            self.add_error("start_date", "The start date cannot be in the past.")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the start date.")
        if scope and oic:
            delegator = scope.employee
            if oic.pk == delegator.pk:
                self.add_error("oic", "The approver cannot be their own OIC.")
            elif not may_be_oic(oic, scope):
                self.add_error("oic", OIC_NOT_ALLOWED)
        if start and end and scope:
            overlap = R.objects.filter(
                is_oic=True, is_active=True, delegated_by=scope.employee, role=scope.role,
                section_id=scope.section_id, unit_id=scope.unit_id, start_date__lte=end,
            ).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
            if overlap.exists():
                raise ValidationError("This approver already has an OIC for this area on some of these dates. End it first.")
        f = cleaned.get("instruction")
        if f:
            try:
                validate_upload(f, 0)
            except ValidationError as exc:
                self.add_error("instruction", exc.messages[0])
        return cleaned


@login_required
def oic_hr_home(request):
    acting = get_acting_employee(request.user)
    if not can_hr_designate_oic(acting):
        raise PermissionDenied("Only an HR Administrator can designate an OIC on an approver's behalf.")

    if request.method == "POST":
        form = HROICForm(request.POST, request.FILES, acting=acting)
        if form.is_valid():
            scope, d = form.cleaned_data["scope"], form.cleaned_data
            ra = R(
                employee=d["oic"], role=scope.role, section=scope.section, unit=scope.unit, is_oic=True,
                delegated_by=scope.employee, start_date=d["start_date"], end_date=d["end_date"],
                created_by=request.user, designated_by_hr=acting, designation_reason=d["reason"].strip(),
                designation_reference=d["reference"].strip(),
                instruction=d["instruction"], instruction_name=d["instruction"].name[:255],
            )
            ra.full_clean()
            ra.save()
            span = f"{ra.start_date:%b %d, %Y} to {ra.end_date:%b %d, %Y}"
            notify(ra.employee, f"HR ({acting.full_name}) designated you OIC ({scope.scope_label}) for "
                                f"{scope.employee.full_name}, {span}.", url="/hris/home/")
            notify(scope.employee, f"HR ({acting.full_name}) designated {ra.employee.full_name} as your OIC "
                                   f"({scope.scope_label}), {span}. You cannot act on this area until it ends.",
                   url="/oic/")
            messages.success(request, f"{ra.employee.full_name} is OIC for {scope.employee.full_name}, {span}.")
            return redirect("oic_hr_home")
    else:
        form = HROICForm(acting=acting)

    today = timezone.localdate()
    made = (R.objects.filter(is_oic=True, designated_by_hr__isnull=False)
            .select_related("employee", "delegated_by", "designated_by_hr", "section", "unit")
            .order_by("-start_date", "-pk")[:30])
    rows = [{"ra": ra, "status": _status(ra, today)} for ra in made]
    return render(request, "accounts/oic_hr.html", {"form": form, "rows": rows})


@login_required
def oic_instruction(request, pk):
    """The written instruction: HR Administrators, the approver, the OIC, the AO and the COH."""
    acting = get_acting_employee(request.user)
    ra = get_object_or_404(R, pk=pk, is_oic=True)
    if not ra.instruction:
        raise Http404("No written instruction on this designation.")
    allowed = acting is not None and (
        acting.pk in (ra.delegated_by_id, ra.employee_id)
        or acting.has_role(R.HR_ADMINISTRATOR, viewing=True)
        or acting.has_role(R.ADMINISTRATIVE_OFFICER, viewing=True)
        or acting.has_role(R.CHIEF_OF_HOSPITAL, viewing=True)
    )
    if not allowed:
        raise PermissionDenied("You are not authorized to open this document.")
    return FileResponse(ra.instruction.open("rb"), filename=ra.instruction_name or "instruction")
