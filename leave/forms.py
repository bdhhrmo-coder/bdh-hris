from django import forms

from .models import LeaveApplication, LeaveType


class LeaveApplicationForm(forms.ModelForm):
    class Meta:
        model = LeaveApplication
        fields = ["leave_type", "start_date", "end_date", "number_of_days", "justification"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "justification": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        if employee is not None:
            applicable = [LeaveType.APPLICABLE_BOTH, employee.employment_status]
            self.fields["leave_type"].queryset = LeaveType.objects.filter(
                is_active=True, applicable_to__in=applicable
            )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        leave_type = cleaned.get("leave_type")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        if leave_type and leave_type.requires_justification and not cleaned.get("justification", "").strip():
            self.add_error("justification", f"{leave_type.name} requires a justification.")
        return cleaned
