from decimal import Decimal

from django import forms

from .balances import compute_credit
from .claims import remaining_claimable_hours
from .models import CTOCreditEntry, CTOMultiplierRate, CTOUsageApplication
from .permissions import is_cto_eligible


def _add_credit_preview(form, cleaned, employee):
    """Shared by both claim forms: the multiplier comes from the
    CTOMultiplierRate active on the work date (configurable, never
    hardcoded — §7). This is a preview; claim_submit recomputes it."""
    work_date = cleaned.get("work_date")
    hours_worked = cleaned.get("hours_worked")
    if not (employee and work_date):
        return
    if not is_cto_eligible(employee):
        form.add_error(None, "CTO is not applicable to the Chief of Hospital.")
        return
    rate = CTOMultiplierRate.active_as_of(work_date)
    if rate is None:
        form.add_error("work_date", "No CTO multiplier rate is configured as of this date.")
    elif hours_worked:
        multiplier, credited_hours, credited_days = compute_credit(
            hours_worked, cleaned.get("is_restday_or_holiday", False), rate, employee.shift_hours,
        )
        cleaned["_multiplier_applied"] = multiplier
        cleaned["_credited_hours"] = credited_hours
        cleaned["_credited_days"] = credited_days
        cleaned["_shift_hours_used"] = employee.shift_hours


class CTOClaimForm(forms.ModelForm):
    """
    HR files a CTO claim for ONE workday of an approved OT/rest-day/holiday
    request (settled 2026-09-28, CLAUDE.md §7). HR enters that day's actual
    hours from the DTR and whether that day was a rest day/holiday; the
    total across all of the OT's claims can't exceed its approved hours.
    """

    class Meta:
        model = CTOCreditEntry
        fields = ["work_date", "duty_type", "hours_worked", "is_restday_or_holiday", "notes"]
        widgets = {"work_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, ot_request, **kwargs):
        super().__init__(*args, **kwargs)
        self.ot_request = ot_request
        self.instance.ot_request = ot_request
        self.instance.employee = ot_request.employee

    def clean(self):
        cleaned = super().clean()
        ot = self.ot_request
        work_date = cleaned.get("work_date")
        hours_worked = cleaned.get("hours_worked")

        if work_date and not (ot.start_date <= work_date <= ot.end_date):
            self.add_error("work_date", f"Must fall within the OT request ({ot.start_date} to {ot.end_date}).")
        elif work_date and ot.cto_claims.filter(work_date=work_date).exists():
            self.add_error("work_date", "A CTO claim already exists for this workday of this OT request.")

        if hours_worked is not None:
            if hours_worked <= 0:
                self.add_error("hours_worked", "Hours worked must be greater than zero.")
            else:
                remaining = remaining_claimable_hours(ot)
                if hours_worked > remaining:
                    self.add_error(
                        "hours_worked",
                        f"Only {remaining} of this OT request's {ot.hours_requested} approved hours are "
                        "left to claim.",
                    )

        _add_credit_preview(self, cleaned, ot.employee)
        return cleaned


class CTOExceptionClaimForm(forms.ModelForm):
    """
    HR Administrator only: a CTO claim with no OT request behind it (e.g.
    certified emergency duty or Medical Transport duty). A reason is
    required; it then goes through the same draft/documents/submit checks
    as any other claim.
    """

    class Meta:
        model = CTOCreditEntry
        fields = [
            "employee", "work_date", "duty_type", "hours_worked", "is_restday_or_holiday",
            "exception_reason", "notes",
        ]
        widgets = {"work_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exception_reason"].required = True

    def clean(self):
        cleaned = super().clean()
        hours_worked = cleaned.get("hours_worked")
        if hours_worked is not None and hours_worked <= 0:
            self.add_error("hours_worked", "Hours worked must be greater than zero.")
        _add_credit_preview(self, cleaned, cleaned.get("employee"))
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
