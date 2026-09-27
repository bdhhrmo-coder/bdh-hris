from decimal import Decimal

from django import forms

from .balances import compute_credit
from .models import CTOCreditEntry, CTOMultiplierRate, CTOUsageApplication
from .permissions import is_cto_eligible


class CTOCreditEntryForm(forms.ModelForm):
    """
    HR's direct-entry form for a verified OT/rest-day/holiday claim (see
    cto/models.py module docstring for why this isn't routed yet).
    """

    class Meta:
        model = CTOCreditEntry
        fields = ["employee", "work_date", "duty_type", "hours_worked", "is_restday_or_holiday", "notes"]
        widgets = {"work_date": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        work_date = cleaned.get("work_date")
        hours_worked = cleaned.get("hours_worked")

        if employee and not is_cto_eligible(employee):
            self.add_error("employee", "CTO is not applicable to the Chief of Hospital.")

        if employee and work_date:
            rate = CTOMultiplierRate.active_as_of(work_date)
            if rate is None:
                self.add_error("work_date", "No CTO multiplier rate is configured as of this date.")
            elif hours_worked:
                multiplier, credited_hours, credited_days = compute_credit(
                    hours_worked, cleaned.get("is_restday_or_holiday", False), rate, employee.shift_hours,
                )
                cleaned["_multiplier_applied"] = multiplier
                cleaned["_credited_hours"] = credited_hours
                cleaned["_credited_days"] = credited_days
                cleaned["_shift_hours_used"] = employee.shift_hours
        return cleaned


class CTOUsageApplicationForm(forms.ModelForm):
    class Meta:
        model = CTOUsageApplication
        fields = ["start_date", "end_date", "number_of_days", "reason"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

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
