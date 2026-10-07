from django import forms

from .models import OfficialRequest


class OfficialRequestForm(forms.ModelForm):
    class Meta:
        model = OfficialRequest
        fields = [
            "request_type", "start_date", "end_date", "time_from", "time_to",
            "destination", "purpose", "hours_requested", "is_restday_or_holiday",
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
        if request_type == OfficialRequest.OT_RESTDAY_HOLIDAY and not cleaned.get("hours_requested"):
            self.add_error("hours_requested", "Required for Authorized OT/restday/holiday work.")
        if request_type == OfficialRequest.TRAVEL and not cleaned.get("destination"):
            self.add_error("destination", "Required for a Travel request.")
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        return cleaned


# -- group (batch) filing, Batch 2 Item 7 -----------------------------------------

class BatchLineForm(forms.Form):
    date = forms.DateField()
    time_from = forms.TimeField(required=False)
    time_to = forms.TimeField(required=False)
    employee = forms.IntegerField(required=False)  # only when "apply same schedule to all" is off
    note = forms.CharField(max_length=255, required=False)


BatchLineFormSet = forms.formset_factory(BatchLineForm, extra=0, min_num=1, validate_min=True)


class BatchForm(forms.Form):
    """The batch header. `entries` (employee x date lines) is built in
    clean() from the employee picker and the date lines."""

    kind = forms.ChoiceField(label="Type")
    employees = forms.ModelMultipleChoiceField(queryset=None, required=True,
                                               error_messages={"required": "Select at least one employee."})
    apply_same = forms.BooleanField(required=False, initial=True)
    purpose = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    destination = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, acting_employee=None, batch=None, line_initial=None, **kwargs):
        from . import batch as batch_rules

        super().__init__(*args, **kwargs)
        self.rules = batch_rules
        self.acting_employee = acting_employee
        self.batch = batch
        kinds = batch_rules.allowed_kinds(acting_employee)
        if batch is not None:  # the type can't change after filing (it decides the route)
            kinds = [k for k, v in batch_rules.KINDS.items()
                     if v[1] == batch.request_type and v[2] == batch.work_kind]
        self.fields["kind"].choices = [(k, batch_rules.KINDS[k][0]) for k in kinds]
        self.fields["employees"].queryset = batch_rules.pickable_employees(acting_employee)
        if self.is_bound:
            self.line_formset = BatchLineFormSet(self.data, prefix="lines")
        else:
            self.line_formset = BatchLineFormSet(prefix="lines", initial=line_initial)
        self.entries = []

    def filer_role(self):
        if self.batch is not None:
            return self.batch.filer_role
        return self.rules.filing_role_for(self.acting_employee, self.cleaned_data.get("kind"))

    def is_valid(self):
        header_ok = super().is_valid()
        lines_ok = self.line_formset.is_valid()
        if header_ok and lines_ok:
            self._build_entries()
        return header_ok and lines_ok and not self.errors

    def clean(self):
        c = super().clean()
        kind = c.get("kind")
        if kind == "TRAVEL" and not (c.get("destination") or "").strip():
            self.add_error("destination", "Required for Travel.")
        employees = c.get("employees")
        if kind and employees is not None:
            role = self.filer_role()
            allowed = set(self.rules.employees_for(self.acting_employee, role).values_list("pk", flat=True))
            outside = [e.full_name for e in employees if e.pk not in allowed]
            if self.acting_employee is not None and any(e.pk == self.acting_employee.pk for e in employees):
                # Including yourself would skip your own Supervisor's check.
                self.add_error("employees", "You can't include yourself - file your own request with the "
                                            "normal form so it follows the normal route.")
            elif outside:
                self.add_error("employees", "You can't file for: " + "; ".join(outside) + ".")
            elif kind == "OFFICIAL_BUSINESS" and role in self.rules.HR_ROLES and employees:
                if not self.rules.common_supervisors(employees, exclude_employee=self.acting_employee):
                    self.add_error("employees", "Official Business filed by HR goes to the employees' Supervisor "
                                                "first, but these employees don't share one Supervisor. File one "
                                                "batch per section.")
        return c

    def _build_entries(self):
        c = self.cleaned_data
        kind = c["kind"]
        employees = list(c["employees"])
        by_id = {e.pk: e for e in employees}
        lines = [f.cleaned_data for f in self.line_formset.forms if f.cleaned_data]
        if len(lines) > self.rules.MAX_DATE_LINES:
            self.add_error(None, f"At most {self.rules.MAX_DATE_LINES} date lines per batch.")
            return
        entries = []
        for i, line in enumerate(lines, start=1):
            if kind in self.rules.WORK_KINDS and not (line.get("time_from") and line.get("time_to")):
                self.add_error(None, f"Line {i}: time start and time end are required for OT / rest-day / holiday work.")
            if line.get("time_from") and line.get("time_to") and line["time_from"] == line["time_to"]:
                self.add_error(None, f"Line {i}: time end must differ from time start.")
            if c.get("apply_same"):
                targets = employees
            else:
                emp = by_id.get(line.get("employee"))
                if emp is None:
                    self.add_error(None, f"Line {i}: choose which selected employee this line is for.")
                    continue
                targets = [emp]
            for emp in targets:
                entries.append({"employee": emp, "date": line["date"], "time_from": line.get("time_from"),
                                "time_to": line.get("time_to"), "note": (line.get("note") or "").strip()})
        keys = [(e["employee"].pk, e["date"]) for e in entries]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            names = sorted({f"{by_id[k[0]].full_name} on {k[1]:%b %d, %Y}" for k in dupes})
            self.add_error(None, "Listed more than once: " + "; ".join(names) + ".")
        if len(entries) > self.rules.MAX_ENTRIES:
            self.add_error(None, f"Too many lines ({len(entries)}); at most {self.rules.MAX_ENTRIES} per batch.")
        self.entries = entries
