from django import forms

from .models import OfficialRequest


class OfficialRequestForm(forms.ModelForm):
    class Meta:
        model = OfficialRequest
        fields = [
            "request_type", "start_date", "end_date", "time_from", "time_to",
            "destination", "purpose",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "time_from": forms.TimeInput(attrs={"type": "time"}),
            "time_to": forms.TimeInput(attrs={"type": "time"}),
            "purpose": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        request_type = cleaned.get("request_type")
        if request_type == OfficialRequest.TRAVEL and not cleaned.get("destination"):
            self.add_error("destination", "Required for a Travel request.")
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        return cleaned
