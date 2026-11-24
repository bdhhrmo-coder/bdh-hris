from django import forms

from .models import LeaveApplication, LeaveType

L = LeaveApplication


class LeaveApplicationForm(forms.ModelForm):
    """
    Leave application, including CSC Form No. 6 (Revised 2020) 6.B Details of
    Leave and 6.D Commutation (Batch 8). Which 6.B fields apply depends on
    the leave type (LeaveApplication.DETAIL_FIELDS_BY_TYPE); the ones that
    apply are required as shown on the CSC form, the rest are cleared, so a
    changed leave type never leaves stale details behind.
    """

    class Meta:
        model = LeaveApplication
        fields = [
            "leave_type",
            "location", "location_detail", "sick_leave_type", "illness", "study_purpose", "wellness_reason",
            "other_purpose",
            "start_date", "end_date", "number_of_days", "commutation", "justification",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "justification": forms.Textarea(attrs={"rows": 3}),
            "location": forms.RadioSelect,
            "sick_leave_type": forms.RadioSelect,
            "study_purpose": forms.RadioSelect,
            "wellness_reason": forms.RadioSelect,
            "other_purpose": forms.RadioSelect,
            "commutation": forms.RadioSelect,
        }
        labels = {
            "leave_type": "6.A Type of leave",
            "location": "In case of Vacation/Special Privilege Leave",
            "location_detail": "Place (specify)",
            "sick_leave_type": "In case of Sick Leave",
            "illness": "Illness (specify)",
            "study_purpose": "In case of Study Leave",
            "wellness_reason": "Reason for Wellness Leave",
            "other_purpose": "Other purpose (optional)",
            "start_date": "Inclusive dates: from",
            "end_date": "to",
            "number_of_days": "6.C Number of working days applied for",
            "commutation": "6.D Commutation",
        }

    # Part 3 (owner 2026-11-19): COSP employees may charge some or all of the
    # leave days to CTO. Not model fields: the CTO days live in a linked CTO
    # record (leave/cto_cover.py). Default: leave credits only.
    use_cto = forms.BooleanField(required=False, label="Charge some of these days to my CTO")
    cto_days = forms.DecimalField(required=False, max_digits=5, decimal_places=2, label="Days charged to CTO",
                                  help_text="Half days (0.5) or whole days. The rest is charged to leave credits.")
    cto_start_date = forms.DateField(required=False, label="CTO from",
                                     widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    cto_end_date = forms.DateField(required=False, label="CTO to",
                                   widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        from .cto_cover import cover_of

        self.offers_cto = employee is not None and employee.employment_status == "COSP"
        if not self.offers_cto:
            for name in ("use_cto", "cto_days", "cto_start_date", "cto_end_date"):
                del self.fields[name]
        elif not self.is_bound:
            cover = cover_of(self.instance)
            if cover is not None:
                self.initial.update(use_cto=True, cto_days=cover.number_of_days,
                                    cto_start_date=cover.start_date, cto_end_date=cover.end_date)
        for name in ("location", "sick_leave_type", "study_purpose", "wellness_reason", "other_purpose"):
            self.fields[name].choices = [c for c in self.fields[name].choices if c[0]]  # no "---------" radio
        self.fields["commutation"].choices = L.COMMUTATION_CHOICES
        self.fields["commutation"].required = False  # hidden for COSP Leave; defaults to Not Requested
        self.fields["start_date"].required = False
        self.fields["end_date"].required = False
        if employee is not None:
            applicable = [LeaveType.APPLICABLE_BOTH, employee.employment_status]
            self.fields["leave_type"].queryset = LeaveType.objects.filter(
                is_active=True, applicable_to__in=applicable
            )

    def type_codes(self):
        """{leave type id: code} for the screen's show/hide logic."""
        return {str(t.pk): t.code for t in self.fields["leave_type"].queryset}

    def clean(self):
        cleaned = super().clean()
        leave_type = cleaned.get("leave_type")
        code = leave_type.code if leave_type else ""
        used = set(L.DETAIL_FIELDS_BY_TYPE.get(code, []))
        for name in L.ALL_DETAIL_FIELDS:
            if name not in used:
                cleaned[name] = ""
        on_csc_form = leave_type is not None and L.route_for(self.employee, leave_type) == L.ROUTE_REGULAR
        if not on_csc_form or not cleaned.get("commutation"):
            cleaned["commutation"] = L.COMMUTATION_NOT_REQUESTED
        purpose = cleaned.get("other_purpose")

        def need(name, message):
            if not (cleaned.get(name) or "").strip():
                self.add_error(name, message)

        if code in ("VL", "SPL"):
            need("location", "Choose Within the Philippines or Abroad.")
            if cleaned.get("location") == L.LOCATION_ABROAD:
                need("location_detail", "Specify the country or place abroad.")
        elif code == "SL":
            need("sick_leave_type", "Choose In Hospital or Out Patient.")
            need("illness", "Specify the illness.")
        elif code == "SPECIAL_WOMEN":
            need("illness", "Specify the illness.")
        elif code == "STUDY":
            need("study_purpose", "Choose Completion of Master's Degree or BAR/Board Examination Review.")
        elif code == "WELLNESS":
            need("wellness_reason", "Choose the reason for your Wellness Leave.")

        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if purpose:
            # Monetization / terminal leave: no leave is taken, so the CSC form
            # has no inclusive dates; commutation is necessarily requested.
            # Confirmed by the project owner (2026-10-16): there are no inclusive dates.
            cleaned["commutation"] = L.COMMUTATION_REQUESTED
            cleaned["start_date"] = cleaned["end_date"] = None
            start = end = None
        else:
            if not start:
                self.add_error("start_date", "This field is required.")
            if not end:
                self.add_error("end_date", "This field is required.")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        if leave_type and leave_type.requires_justification and not (cleaned.get("justification") or "").strip():
            self.add_error("justification", f"{leave_type.name} requires a justification.")
        if self.offers_cto and cleaned.get("use_cto"):
            from .cto_cover import may_use_cto, shape_error

            if not may_use_cto(self.employee, leave_type):
                self.add_error("use_cto", "CTO cannot be charged for this leave.")
            else:
                problem = shape_error(cleaned.get("number_of_days"), start, end, cleaned.get("cto_days"),
                                      cleaned.get("cto_start_date"), cleaned.get("cto_end_date"))
                if problem:
                    self.add_error("cto_days", problem)
        return cleaned
