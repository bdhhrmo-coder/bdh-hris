from django import forms
from django.core.exceptions import ValidationError

from documents.validators import validate_upload

from .models import OutOnPass


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
