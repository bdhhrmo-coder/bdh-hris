from decimal import Decimal

from django import forms

from employees.models import Employee

from .models import DAY_TYPE_CHOICES, CTOClaim, CTOUsageApplication, OvertimeRequest

DATE = forms.DateInput(attrs={"type": "date"})


class OvertimeRequestForm(forms.Form):
    filed_as = forms.ChoiceField(choices=OvertimeRequest.CAPACITY_CHOICES)
    purpose = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    past_date_reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Required when any line is dated before today.",
    )

    def __init__(self, *args, capacities, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filed_as"].choices = [c for c in OvertimeRequest.CAPACITY_CHOICES if c[0] in capacities]


class OvertimeLineForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    work_date = forms.DateField(widget=DATE)
    approved_hours = forms.DecimalField(max_digits=5, decimal_places=2)
    day_type = forms.ChoiceField(choices=DAY_TYPE_CHOICES)


# 3 rows to start (1 required + 2 extra); the page's "Add row" button adds more.
OvertimeLineFormSet = forms.formset_factory(OvertimeLineForm, extra=2, min_num=1, validate_min=True, max_num=200)


class ClaimStartForm(forms.Form):
    duty_type = forms.ChoiceField(choices=CTOClaim.DUTY_TYPE_CHOICES)


class ExceptionClaimForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    duty_type = forms.ChoiceField(choices=CTOClaim.DUTY_TYPE_CHOICES)
    exception_reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class OTLineChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        from .claims import ot_day_type
        from .models import DAY_TYPE_CHOICES

        where = f"group request #{obj.batch_id}" if obj.batch_id else f"request #{obj.pk}"
        return f"{obj.start_date:%b %d, %Y} — {obj.hours_requested} h approved — {dict(DAY_TYPE_CHOICES)[ot_day_type(obj)]} ({where})"


class OTClaimLineForm(forms.Form):
    ot_line = OTLineChoiceField(queryset=None, label="Approved OT line")
    hours_worked = forms.DecimalField(max_digits=5, decimal_places=2, label="Actual hours (from DTR)")

    def __init__(self, *args, ot_lines, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ot_line"].queryset = ot_lines


class ManualClaimLineForm(forms.Form):
    work_date = forms.DateField(widget=DATE)
    hours_worked = forms.DecimalField(max_digits=5, decimal_places=2)
    day_type = forms.ChoiceField(choices=DAY_TYPE_CHOICES)


class OpeningLineForm(forms.Form):
    days = forms.DecimalField(max_digits=6, decimal_places=2)
    date_earned = forms.DateField(widget=DATE)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), help_text="Cite the paper record.")


class SeparationForm(forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))
    separation_status = forms.ChoiceField(
        choices=[("", "— none (clear) —")] + Employee.SEPARATION_STATUS_CHOICES, required=False
    )
    last_day_of_service = forms.DateField(widget=DATE, required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class CTOUsageApplicationForm(forms.ModelForm):
    class Meta:
        model = CTOUsageApplication
        fields = ["start_date", "end_date", "number_of_days", "reason"]
        widgets = {"start_date": DATE, "end_date": DATE}

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        number_of_days = cleaned.get("number_of_days")

        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")

        if number_of_days is not None and (number_of_days <= 0 or number_of_days % Decimal("0.5") != 0):
            self.add_error("number_of_days", "CTO must be used in half-day (0.5) or full-day (1.0+) blocks.")

        return cleaned
