from django import forms
from django.core.exceptions import ValidationError

from documents.validators import validate_upload

from .models import CospContractPeriod, OutOnPass, PassOpeningUsage


class PassForm(forms.ModelForm):
    """Part I of the slip. The rules themselves are checked in the view
    (rules.check_pass), which needs the employee and the time of filing."""

    proof = forms.FileField(
        required=False, label="Proof (PDF, JPG or PNG, up to 10 MB)",
        help_text="Required for a third pass. Seen only by you, your supervisor and the approver.")

    class Meta:
        model = OutOnPass
        fields = ["kind", "pass_date", "time_out", "time_in", "reason_type", "reason"]
        labels = {"kind": "Type of pass", "pass_date": "Date", "time_out": "Time out",
                  "time_in": "Expected time in"}
        widgets = {
            "kind": forms.RadioSelect,
            "pass_date": forms.DateInput(attrs={"type": "date"}),
            "time_out": forms.TimeInput(attrs={"type": "time"}),
            "time_in": forms.TimeInput(attrs={"type": "time"}),
            "reason": forms.Textarea(attrs={"rows": 2}),
        }
        help_texts = {"time_in": "", "reason": "What the pass is for, briefly."}

    def __init__(self, *args, has_proof=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_proof = has_proof

    def clean_proof(self):
        f = self.cleaned_data.get("proof")
        if f:
            try:
                self.proof_file_type = validate_upload(f, 0)
            except ValidationError as exc:
                raise ValidationError(exc.message)
        return f

    def clean(self):
        data = super().clean()
        if data.get("kind") == OutOnPass.THIRD and not data.get("proof") and not self.has_proof:
            self.add_error("proof", "Attach the proof of the urgent reason. A third pass without proof is not processed.")
        return data


class ActionForm(forms.Form):
    """The approver's step. The Supervisor step also confirms what the
    system cannot see (CLAUDE.md 13B)."""

    action = forms.CharField()
    notes = forms.CharField(required=False, max_length=255)
    confirmed_coverage = forms.BooleanField(required=False)
    confirmed_no_surge = forms.BooleanField(required=False)
    confirmed_shift_hours = forms.BooleanField(required=False)
    covering_staff = forms.CharField(required=False, max_length=255)


class ContractForm(forms.ModelForm):
    class Meta:
        model = CospContractPeriod
        fields = ["employee", "start_date", "end_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}),
                   "end_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, acting=None, **kwargs):
        super().__init__(*args, **kwargs)
        from employees.models import Employee
        self.acting = acting
        self.fields["employee"].queryset = Employee.objects.filter(
            employment_status=Employee.EMPLOYMENT_STATUS_COSP, archived_at__isnull=True).exclude(pk=getattr(acting, "pk", None))
        self.fields["employee"].help_text = "COSP and PGSS personnel. Your own record is entered by another HR Administrator."

    def clean(self):
        data = super().clean()
        e, start, end = data.get("employee"), data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            raise ValidationError("The end date is before the start date.")
        if e and start and end and e.cosp_contract_periods.filter(
                voided_at__isnull=True, start_date__lte=end, end_date__gte=start).exists():
            raise ValidationError("This overlaps a contract period already entered for this employee.")
        return data


class OpeningForm(forms.ModelForm):
    hours = forms.IntegerField(min_value=0, max_value=24, initial=0)
    minutes = forms.IntegerField(min_value=0, max_value=59, initial=0)

    class Meta:
        model = PassOpeningUsage
        fields = ["employee", "year", "passes_used_in_month", "reason"]
        labels = {"passes_used_in_month": "Passes already taken in January"}

    def __init__(self, *args, acting=None, **kwargs):
        super().__init__(*args, **kwargs)
        from employees.models import Employee
        self.fields["employee"].queryset = Employee.objects.filter(archived_at__isnull=True).exclude(
            pk=getattr(acting, "pk", None))
        self.fields["year"].initial = 2027
        self.fields["reason"].initial = "Google Sheets tracker, Jan 1-10, 2027"

    def clean(self):
        data = super().clean()
        e, year = data.get("employee"), data.get("year")
        if e and year and e.pass_opening_usage.filter(year=year, voided_at__isnull=True).exists():
            raise ValidationError("This employee already has a line for that year. Void it first to correct it.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.minutes_used = self.cleaned_data["hours"] * 60 + self.cleaned_data["minutes"]
        if commit:
            obj.save()
        return obj
