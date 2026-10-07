from django import forms
from django.utils import timezone

from employees.models import Employee

from .models import AttendanceCorrectionLine, AttendanceCorrectionRequest


class BiometricImportForm(forms.Form):
    file = forms.FileField(help_text="CSV or Excel (.xlsx) export from the biometric system.")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".csv", ".xlsx", ".xlsm")):
            raise forms.ValidationError("Only .csv, .xlsx, or .xlsm files are accepted.")
        return f


# -- correction lines (Batch 2, Item 6) ---------------------------------------

class CorrectionLineForm(forms.Form):
    """One date. `formal` decides whether the reason is a Missed Log
    Justification category (+ details) or HR's free-text reason."""

    date = forms.DateField()
    time_in = forms.TimeField(required=False)
    time_out = forms.TimeField(required=False)
    overnight = forms.BooleanField(required=False)
    is_absent = forms.BooleanField(required=False)
    reason_category = forms.ChoiceField(
        choices=[("", "— choose —")] + AttendanceCorrectionRequest.REASON_CATEGORY_CHOICES, required=False,
    )
    reason = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, formal=True, **kwargs):
        self.formal = formal
        super().__init__(*args, **kwargs)

    def clean(self):
        c = super().clean()
        day, t_in, t_out = c.get("date"), c.get("time_in"), c.get("time_out")
        if day and day > timezone.localdate():
            self.add_error("date", "Future dates can't be corrected.")
        if c.get("is_absent"):
            if t_in or t_out:
                self.add_error(None, "Tick 'Absent' OR give the times, not both.")
        elif not t_in and not t_out:
            self.add_error(None, "Give the correct time in and/or time out (or tick 'Absent').")
        if t_in and t_out and t_out <= t_in and not c.get("overnight"):
            self.add_error("time_out", "Time out must be later than time in (tick 'Overnight' for a shift that ends the next day).")
        reason = (c.get("reason") or "").strip()
        if self.formal:
            category = c.get("reason_category")
            if not category:
                self.add_error("reason_category", "Choose a reason.")
            elif category in AttendanceCorrectionRequest.REASONS_NEEDING_DETAILS and not reason:
                self.add_error("reason", "Please specify the meeting/activity/training or other reason.")
        elif not reason:
            self.add_error("reason", "Give the reason for this correction.")
        return c


class BaseCorrectionLineFormSet(forms.BaseFormSet):
    def __init__(self, *args, formal=True, employee=None, exclude_request=None, **kwargs):
        self.formal = formal
        self.employee = employee
        self.exclude_request = exclude_request
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {"formal": self.formal}

    @property
    def empty_form(self):
        return self.form(auto_id=self.auto_id, prefix=self.add_prefix("__prefix__"), empty_permitted=True,
                         use_required_attribute=False, formal=self.formal, renderer=self.renderer)

    def clean(self):
        if any(self.errors):
            return
        lines = [f.cleaned_data for f in self.forms if f.cleaned_data]
        dates = [line["date"] for line in lines]
        duplicates = sorted({d for d in dates if dates.count(d) > 1})
        if duplicates:
            raise forms.ValidationError(
                "The same date is listed more than once: " + ", ".join(f"{d:%b %d, %Y}" for d in duplicates) + "."
            )
        if self.employee is not None and dates:
            pending = AttendanceCorrectionLine.objects.filter(
                request__employee=self.employee, date__in=dates,
                request__status__in=AttendanceCorrectionRequest.OPEN_STATUSES,
            )
            if self.exclude_request is not None:
                pending = pending.exclude(request=self.exclude_request)
            taken = sorted({line.date for line in pending})
            if taken:
                raise forms.ValidationError(
                    "These dates already have a pending correction request: "
                    + ", ".join(f"{d:%b %d, %Y}" for d in taken) + "."
                )
        if self.formal:
            groups = {AttendanceCorrectionRequest.validator_for_reason(line["reason_category"]) for line in lines}
            if len(groups) > 1:
                raise forms.ValidationError(
                    "Offline / Failed Attempt / Wrong Button dates (validated by ICTU) and meeting / activity / "
                    "other dates (validated by HR) must be filed as separate requests."
                )


CorrectionLineFormSet = forms.formset_factory(
    CorrectionLineForm, formset=BaseCorrectionLineFormSet, extra=0,
    min_num=1, max_num=AttendanceCorrectionRequest.MAX_LINES, validate_min=True, validate_max=True,
)


def line_formset(data=None, *, formal, employee=None, request_obj=None):
    """Bound formset from POST data, or unbound with the request's current
    lines (edit after a return) / one empty line (new request)."""
    kwargs = {"prefix": "lines", "formal": formal, "employee": employee, "exclude_request": request_obj}
    if data is not None:
        return CorrectionLineFormSet(data, **kwargs)
    initial = None
    if request_obj is not None:
        initial = [
            {"date": line.date, "time_in": line.time_in, "time_out": line.time_out, "overnight": line.overnight,
             "is_absent": line.is_absent, "reason_category": line.reason_category, "reason": line.reason}
            for line in request_obj.sorted_lines
        ]
    return CorrectionLineFormSet(initial=initial, **kwargs)


def formset_rows(formset):
    """Rows for the Alpine line editor: current values + errors per line."""
    rows = []
    forms_ = formset.forms if formset.forms else []
    for f in forms_:
        def val(name):
            if f.is_bound:
                v = f.data.get(f.add_prefix(name), "")
                if name in ("overnight", "is_absent"):
                    return v in ("on", "true", "True", "1")
                return v
            v = f.initial.get(name)
            if name in ("overnight", "is_absent"):
                return bool(v)
            if v is None:
                return ""
            if name in ("time_in", "time_out"):
                return v.strftime("%H:%M")
            return str(v)
        errors = []
        if f.is_bound:
            for field, errs in f.errors.items():
                errors.extend(errs)
        rows.append({name: val(name) for name in
                     ("date", "time_in", "time_out", "overnight", "is_absent", "reason_category", "reason")}
                    | {"errors": errors})
    return rows or [{"date": "", "time_in": "", "time_out": "", "overnight": False, "is_absent": False,
                     "reason_category": "", "reason": "", "errors": []}]


def save_lines(correction, formset):
    """Replace the request's lines with the formset's (new request, or a
    resubmission after a return)."""
    correction.lines.all().delete()
    for data in (f.cleaned_data for f in formset.forms if f.cleaned_data):
        AttendanceCorrectionLine.objects.create(
            request=correction, date=data["date"], time_in=data.get("time_in"), time_out=data.get("time_out"),
            overnight=bool(data.get("overnight")), is_absent=bool(data.get("is_absent")),
            reason_category=data.get("reason_category") or "" if formset.formal else "",
            reason=(data.get("reason") or "").strip(),
        )
    if formset.formal:
        first = next(f.cleaned_data for f in formset.forms if f.cleaned_data)
        correction.validator = AttendanceCorrectionRequest.validator_for_reason(first["reason_category"])
        correction.save(update_fields=["validator", "updated_at"])


class MinorEmployeeForm(forms.Form):
    """HR's minor correction: whose record is being fixed."""

    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True))


class ActionForm(forms.Form):
    """Approve / validate / return / reject the WHOLE request. A return
    needs a remark (Batch 2, Item 6); the remark is optional otherwise."""

    action = forms.CharField()
    notes = forms.CharField(max_length=255, required=False)

    def clean(self):
        c = super().clean()
        if c.get("action") == "return" and not (c.get("notes") or "").strip():
            self.add_error("notes", "Type the reason for returning it, so the filer knows what to fix.")
        return c
