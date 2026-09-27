from django import forms

from .models import DutyExchangeRequest
from .rules import exceeds_monthly_cap, violates_minimum_notice


class DutyExchangeRequestForm(forms.ModelForm):
    """Employee A's initiating form — Employee B consents separately (see
    exchange/views.py)."""

    class Meta:
        model = DutyExchangeRequest
        fields = ["employee_b", "date_a", "date_b", "reason", "is_emergency", "emergency_justification"]
        widgets = {
            "date_a": forms.DateInput(attrs={"type": "date"}),
            "date_b": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, employee_a=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee_a = employee_a
        if employee_a is not None:
            self.fields["employee_b"].queryset = self.fields["employee_b"].queryset.exclude(pk=employee_a.pk)

    def clean(self):
        cleaned = super().clean()
        date_a = cleaned.get("date_a")
        date_b = cleaned.get("date_b")
        is_emergency = cleaned.get("is_emergency", False)

        if date_a and date_a == date_b:
            self.add_error("date_b", "Employee B's date must differ from Employee A's date.")

        if is_emergency and not cleaned.get("emergency_justification"):
            self.add_error("emergency_justification", "Required when filing as a documented emergency.")

        if date_a and date_b and self.employee_a is not None:
            earliest = min(date_a, date_b)
            from datetime import date as date_cls

            if violates_minimum_notice(earliest, date_cls.today(), is_emergency):
                self.add_error(
                    None,
                    "This request gives less than 24 hours' notice before the earliest affected date. "
                    "Check 'Documented emergency' if this is a genuine emergency (§8).",
                )
            if exceeds_monthly_cap(self.employee_a):
                self.add_error(None, "You have already reached the 3-exchange-request-per-month limit (§8).")

        employee_b = cleaned.get("employee_b")
        if employee_b is not None and exceeds_monthly_cap(employee_b):
            self.add_error(
                "employee_b", f"{employee_b} has already reached the 3-exchange-request-per-month limit (§8)."
            )

        return cleaned
