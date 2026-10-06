from django import forms

from .models import AttendanceCorrectionRequest


class BiometricImportForm(forms.Form):
    file = forms.FileField(help_text="CSV or Excel (.xlsx) export from the biometric system.")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            raise forms.ValidationError("Only .csv, .xlsx, or .xlsm files are accepted.")
        return f


class MinorCorrectionForm(forms.ModelForm):
    """HR-initiated correction — filed on the employee's behalf."""

    class Meta:
        model = AttendanceCorrectionRequest
        fields = ["employee", "date", "requested_time_in", "requested_time_out", "requested_is_absent", "reason"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "requested_time_in": forms.TimeInput(attrs={"type": "time"}),
            "requested_time_out": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = True
        self.fields["reason"].label = "Reason"


class FormalCorrectionForm(forms.ModelForm):
    """Employee-initiated correction — always for the filer's own record."""

    class Meta:
        model = AttendanceCorrectionRequest
        fields = [
            "date", "requested_time_in", "requested_time_out", "requested_is_absent", "reason_category", "reason",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "requested_time_in": forms.TimeInput(attrs={"type": "time"}),
            "requested_time_out": forms.TimeInput(attrs={"type": "time"}),
            "reason_category": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.initial["reason_category"] = None  # make the employee pick one; it decides the validator
