from django import forms

from .models import DutyExchangeRequest
from .rules import exceeds_monthly_cap, violates_minimum_notice

X = DutyExchangeRequest


class DutyExchangeRequestForm(forms.ModelForm):
    """Employee A's initiating form — Employee B consents separately (see
    exchange/views.py). The kind (regular / change to an approved schedule /
    emergency) is decided here from the month's schedule (exchange/regular.py,
    owner decisions 2026-11-03) and saved on the request."""

    class Meta:
        model = DutyExchangeRequest
        fields = ["employee_b", "date_a", "shift_a", "date_b", "shift_b", "reason", "is_emergency",
                  "emergency_justification"]
        widgets = {
            "date_a": forms.DateInput(attrs={"type": "date"}),
            "date_b": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "date_a": "Your duty date (the other employee works it)",
            "shift_a": "Your shift on that date",
            "date_b": "Their duty date (you work it)",
            "shift_b": "Their shift on that date",
        }
        help_texts = {
            "shift_a": "Needed when that month's schedule is not submitted yet (regular exchange). "
                       "Otherwise it is read from the approved schedule.",
            "shift_b": "As above.",
        }

    def __init__(self, *args, employee_a=None, **kwargs):
        from schedules.models import ShiftCode

        super().__init__(*args, **kwargs)
        self.employee_a = employee_a
        self.kind = None
        if employee_a is not None:
            self.fields["employee_b"].queryset = self.fields["employee_b"].queryset.exclude(pk=employee_a.pk)
        duty = ShiftCode.objects.filter(is_active=True, is_duty=True)
        for name in ("shift_a", "shift_b"):
            self.fields[name].queryset = duty
            self.fields[name].required = False

    def clean(self):
        from datetime import date as date_cls

        from .regular import kind_for, overlap_errors, regular_errors
        from .schedule_checks import check

        cleaned = super().clean()
        date_a, date_b = cleaned.get("date_a"), cleaned.get("date_b")
        employee_b = cleaned.get("employee_b")
        is_emergency = cleaned.get("is_emergency", False)
        today = date_cls.today()
        self.schedule_notes = []

        if date_a and date_a == date_b:
            self.add_error("date_b", "Employee B's date must differ from Employee A's date.")
        if is_emergency and not cleaned.get("emergency_justification"):
            self.add_error("emergency_justification", "Required when filing as a documented emergency.")

        if date_a and date_b and employee_b is not None and self.employee_a is not None and date_a != date_b:
            kind, errors = kind_for(self.employee_a, date_a, employee_b, date_b, is_emergency)
            for e in errors:
                self.add_error(None, e)
            self.kind = kind
            if kind == X.KIND_REGULAR:
                # No schedule yet: the employees give the shifts; checked against
                # the filing deadline and other exchanges, not a schedule.
                errs, _ = check(self.employee_a, date_a, employee_b, date_b, today, True, with_timing=False)
                for e in [e for e in errs if "same unit" in e]:
                    self.add_error(None, e)
                for e in regular_errors(self.employee_a, date_a, employee_b, date_b, cleaned.get("shift_a"),
                                        cleaned.get("shift_b"), today):
                    self.add_error(None, e)
            elif kind is not None:
                if violates_minimum_notice(min(date_a, date_b), today, is_emergency):
                    self.add_error(None, "This request gives less than 24 hours' notice before the earliest "
                                         "affected date. Check 'Documented emergency' if this is a genuine "
                                         "emergency (§8).")
                errs, notes = check(self.employee_a, date_a, employee_b, date_b, today, is_emergency,
                                    with_timing=False)
                for e in errs + overlap_errors(self.employee_a, date_a, employee_b, date_b):
                    self.add_error(None, e)
                self.schedule_notes = notes

        if self.employee_a is not None and exceeds_monthly_cap(self.employee_a):
            self.add_error(None, "You have already reached the 3-exchange-request-per-month limit (§8).")
        if employee_b is not None and exceeds_monthly_cap(employee_b):
            self.add_error(
                "employee_b", f"{employee_b} has already reached the 3-exchange-request-per-month limit (§8)."
            )
        return cleaned

    def save(self, commit=True):
        from schedules.services import cell_for

        obj = super().save(commit=False)
        obj.kind = self.kind or (X.KIND_EMERGENCY if obj.is_emergency else X.KIND_MODIFICATION)
        if obj.kind != X.KIND_REGULAR:
            # Read the shifts from the schedule, for the record and the form.
            for date_field, emp, shift_field in (("date_a", self.employee_a, "shift_a"),
                                                 ("date_b", obj.employee_b, "shift_b")):
                _, cell = cell_for(emp, getattr(obj, date_field))
                setattr(obj, shift_field, cell.shift if cell is not None and cell.shift_id else None)
        if commit:
            obj.save()
        return obj
