from django import forms
from django.utils import timezone

from .models import OfficialRequest


class OfficialRequestForm(forms.ModelForm):
    """One form for the three request types (owner decisions 2026-11-03):
    Official Business (Travel Order), Local Official Errand and Authorized
    OT / rest day / holiday work. Official Time is no longer offered."""

    class Meta:
        model = OfficialRequest
        fields = [
            "request_type", "start_date", "end_date", "time_from", "time_to",
            "destination", "expenses_authorized", "transportation_allowed",
            "purpose", "hours_requested", "is_restday_or_holiday",
        ]
        labels = {
            "time_from": "Time from", "time_to": "Time to",
            "expenses_authorized": "Expenses authorized",
            "transportation_allowed": "Transportation allowed",
            "is_restday_or_holiday": "Falls on a rest day or holiday",
        }
        help_texts = {
            "time_from": "", "destination": "",
            "expenses_authorized": "Per Diem goes through the Provincial Budget Office before the Governor.",
            "transportation_allowed": "For example: Government Vehicle, Public Transportation.",
            "hours_requested": "Hours worked.", "is_restday_or_holiday": "",
        }
        widgets = {
            "request_type": forms.Select(attrs={"x-model": "t"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "time_from": forms.TimeInput(attrs={"type": "time"}),
            "time_to": forms.TimeInput(attrs={"type": "time"}),
            "purpose": forms.Textarea(attrs={"rows": 3}),
        }

    # Local Official Errand: the vehicle for F59 "Type of Transport" (2026-11-03),
    # stored in transportation_allowed.
    vehicle = forms.ChoiceField(label="Type of transport", required=False, choices=[])

    def __init__(self, *args, acting_employee=None, **kwargs):
        from .f59 import VEHICLE_CHOICES

        super().__init__(*args, **kwargs)
        self.fields["vehicle"].choices = VEHICLE_CHOICES
        if self.instance.pk and self.instance.request_type == OfficialRequest.TRAVEL:
            self.initial.setdefault("vehicle", self.instance.transportation_allowed)
        labels = dict(OfficialRequest.REQUEST_TYPE_CHOICES)
        codes = list(OfficialRequest.ACTIVE_REQUEST_TYPES)
        # 8a (2026-11-02): OT is filed for employees as a group request by a
        # Supervisor, HR or the AO. Only the COH files their own OT here, and
        # HR records it (approval is outside BDH).
        if acting_employee is None or not acting_employee.has_role("CHIEF_OF_HOSPITAL"):
            codes.remove(OfficialRequest.OT_RESTDAY_HOLIDAY)
        self.fields["request_type"].choices = [("", "---------")] + [(code, labels[code]) for code in codes]
        self.fields["transportation_allowed"].initial = "Government Vehicle"

    def clean(self):
        cleaned = super().clean()
        request_type = cleaned.get("request_type")
        if request_type == OfficialRequest.OT_RESTDAY_HOLIDAY and not cleaned.get("hours_requested"):
            self.add_error("hours_requested", "Required for Authorized OT/restday/holiday work.")
        if request_type in (OfficialRequest.TRAVEL, OfficialRequest.OFFICIAL_BUSINESS) \
                and not (cleaned.get("destination") or "").strip():
            self.add_error("destination", "Required for this request type.")
        if request_type == OfficialRequest.OFFICIAL_BUSINESS:
            if not cleaned.get("expenses_authorized"):
                self.add_error("expenses_authorized", "Choose Per Diem or Official Time.")
            if not (cleaned.get("transportation_allowed") or "").strip():
                self.add_error("transportation_allowed", "Required for a Travel Order.")
        elif request_type == OfficialRequest.TRAVEL:
            cleaned["expenses_authorized"] = ""
            cleaned["transportation_allowed"] = cleaned.get("vehicle") or ""
        else:
            cleaned["expenses_authorized"] = ""
            cleaned["transportation_allowed"] = ""
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
    expenses_authorized = forms.ChoiceField(choices=[("", "")] + OfficialRequest.EXPENSES_CHOICES, required=False)
    transportation_allowed = forms.CharField(max_length=100, required=False)
    vehicle = forms.ChoiceField(required=False, choices=[])  # errand: F59 Type of Transport (2026-11-03)
    past_date_reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def __init__(self, *args, acting_employee=None, batch=None, line_initial=None, **kwargs):
        from . import batch as batch_rules

        from .f59 import VEHICLE_CHOICES

        super().__init__(*args, **kwargs)
        self.fields["vehicle"].choices = VEHICLE_CHOICES
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
        if kind in ("TRAVEL", "OFFICIAL_BUSINESS") and not (c.get("destination") or "").strip():
            self.add_error("destination", "Required for this type.")
        if kind == "OFFICIAL_BUSINESS":
            if not c.get("expenses_authorized"):
                self.add_error("expenses_authorized", "Choose Per Diem or Official Time.")
            if not (c.get("transportation_allowed") or "").strip():
                self.add_error("transportation_allowed", "Required for a Travel Order.")
        elif kind == "TRAVEL":
            c["transportation_allowed"] = c.get("vehicle") or ""
        employees = c.get("employees")
        if kind and employees is not None:
            # Scope comes from the roles held NOW. A returned batch is edited
            # only under the same role it was filed with, which decides its
            # route; otherwise file a new batch (security review 2026-11-02).
            role = self.rules.filing_role_for(self.acting_employee, kind)
            if role is None or (self.batch is not None and role != self.batch.filer_role):
                self.add_error("kind", "Your roles have changed since this was filed - file a new group request.")
                return c
            allowed = set(self.rules.employees_for(self.acting_employee, role, kind).values_list("pk", flat=True))
            outside = [e.full_name for e in employees if e.pk not in allowed]
            if self.acting_employee is not None and any(e.pk == self.acting_employee.pk for e in employees):
                # Including yourself would skip your own Supervisor's check.
                self.add_error("employees", "You can't include yourself - file your own request with the "
                                            "normal form so it follows the normal route.")
            elif outside:
                self.add_error("employees", "You can't file for: " + "; ".join(outside) + ".")
            elif any(e.has_role("CHIEF_OF_HOSPITAL") for e in employees):
                # Owner decision 2026-10-26: the COH's own requests are only
                # recorded by HR (approval is outside BDH), via the normal form.
                self.add_error("employees", "Leave out the Chief of Hospital - the COH files their own request "
                                            "with the normal form and HR records it (approval is outside BDH).")
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
        if kind in self.rules.WORK_KINDS:
            self._check_ot_dates(entries, c)
        keys = [(e["employee"].pk, e["date"]) for e in entries]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            names = sorted({f"{by_id[k[0]].full_name} on {k[1]:%b %d, %Y}" for k in dupes})
            self.add_error(None, "Listed more than once: " + "; ".join(names) + ".")
        if len(entries) > self.rules.MAX_ENTRIES:
            self.add_error(None, f"Too many lines ({len(entries)}); at most {self.rules.MAX_ENTRIES} per batch.")
        self.entries = entries

    def _check_ot_dates(self, entries, c):
        """OT (CLAUDE.md §7.1): past dates need a typed reason; no line after
        the employee's last day of service."""
        today = timezone.localdate()
        if any(e["date"] < today for e in entries) and not (c.get("past_date_reason") or "").strip():
            self.add_error("past_date_reason", "Some dates are in the past - type why this is filed late.")
        late = sorted({f'{e["employee"].full_name} on {e["date"]:%b %d, %Y} (last day of service '
                       f'{e["employee"].last_day_of_service:%b %d, %Y})'
                       for e in entries
                       if e["employee"].last_day_of_service and e["date"] > e["employee"].last_day_of_service})
        if late:
            self.add_error(None, "After the last day of service: " + "; ".join(late) + ".")
